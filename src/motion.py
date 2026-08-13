"""The only thing in this service that talks to the camera.

One worker thread owns every dvrip command, so two commands can never
interleave and the HTTP handlers never wait on the camera.

The rules this module keeps, in order of importance:

1. **A motor that starts always stops.** The worker claims a move before it
   writes it to the wire, so a move that fails part way still leaves the worker
   holding an obligation to stop.
2. **A stop is retried until it succeeds.** A failed stop marks the running
   direction unknown, and an unknown direction stops through the full four
   direction sweep, which is the documented remedy for a runaway.
3. **A move has a deadline.** ONVIF ContinuousMove runs until Stop arrives. If
   Stop never arrives, because a browser tab closed or Frigate died, the
   watchdog stops the camera at max_move_seconds.
4. **A move only ever comes from a press.** The worker never repeats a failed
   move. It retries stops and nothing else.

The dvrip session stays open for the length of one move and closes when the
camera is idle. Holding it makes the stop one round trip instead of a login
plus a round trip, and it is not a second continuous client on the camera:
go2rtc holds the stream, and this session exists only between a press and its
release.
"""

import logging
import threading
import time
from dataclasses import dataclass, replace

from ptzcore import DIRECTIONS, clamp_step, connect, send_move, send_stop, send_stop_all

logger = logging.getLogger(__name__)

# How long to wait before retrying a stop that failed. Short, because the camera
# is moving while we retry.
RETRY_SECONDS = 0.25

# How long to wait for the worker to settle the camera during shutdown.
SHUTDOWN_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class Move:
    direction: str
    step: int


@dataclass(frozen=True)
class Status:
    """A read only snapshot, for the health endpoint."""

    moving: bool
    direction: str
    moves_started: int
    stops_completed: int
    watchdog_stops: int
    stop_failures: int
    last_error: str


class MotionController:
    def __init__(self, max_move_seconds: float) -> None:
        self._max_move_seconds = max_move_seconds

        self._condition = threading.Condition()
        self._desired: Move | None = None
        self._deadline = 0.0
        self._shutdown = False

        # Owned by the worker thread alone.
        self._engaged: Move | None = None
        # True means the camera may be moving in a direction we cannot name, so
        # the next stop must sweep all four. It starts True because a restart
        # may have interrupted a move that this process never saw.
        self._engaged_unknown = True
        self._camera = None

        self._counters = {
            "moves_started": 0,
            "stops_completed": 0,
            "watchdog_stops": 0,
            "stop_failures": 0,
        }
        self._last_error = ""

        self._thread = threading.Thread(
            target=self._run, name="motion", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def request_move(self, direction: str, step: int, timeout_seconds: float) -> None:
        """Start a move. It runs until request_stop, or until the deadline."""
        if direction not in DIRECTIONS:
            raise ValueError(f"unknown direction: {direction}")

        limit = min(timeout_seconds, self._max_move_seconds)
        with self._condition:
            self._desired = Move(direction=direction, step=clamp_step(step))
            self._deadline = time.monotonic() + limit
            self._condition.notify_all()

    def request_stop(self) -> None:
        """Stop the camera. Safe to call when the camera is already stopped."""
        with self._condition:
            self._desired = None
            self._condition.notify_all()

    def status(self) -> Status:
        with self._condition:
            engaged = self._engaged
            direction = ""
            if engaged is not None:
                direction = engaged.direction

            return Status(
                moving=engaged is not None or self._engaged_unknown,
                direction=direction,
                last_error=self._last_error,
                **self._counters,
            )

    def shutdown(self) -> None:
        """Stop the camera, then let the worker finish.

        A restart must never leave a motor running, so this waits for the stop
        to land before the process exits.
        """
        with self._condition:
            self._desired = None
            self._shutdown = True
            self._condition.notify_all()

        self._thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            logger.error("the worker did not settle the camera before shutdown")

    # ---------------------------------------------------------------- worker

    def _run(self) -> None:
        while True:
            with self._condition:
                desired = self._expire_locked()

                if self._settled(desired):
                    if self._shutdown:
                        return

                    self._condition.wait(self._idle_wait(desired))
                    continue

            # Talk to the camera outside the lock, so a slow camera never
            # blocks an HTTP handler.
            self._apply(desired)

    def _expire_locked(self) -> Move | None:
        """Drop a move whose deadline passed. The caller holds the lock."""
        if self._desired is None:
            return None

        if time.monotonic() < self._deadline:
            return self._desired

        logger.warning(
            "watchdog: no Stop arrived within %.1fs, stopping the camera",
            self._max_move_seconds,
        )
        self._desired = None
        self._counters["watchdog_stops"] += 1

        return None

    def _settled(self, desired: Move | None) -> bool:
        if self._engaged_unknown:
            return False

        return self._engaged == desired

    def _idle_wait(self, desired: Move | None) -> float | None:
        """How long the worker may sleep before it must look again."""
        if desired is None:
            return None

        # Wake in time to run the watchdog.
        return max(0.0, self._deadline - time.monotonic())

    def _apply(self, desired: Move | None) -> None:
        if desired is None:
            self._disengage()

            return

        if self._engaged is not None and self._engaged != desired:
            # A direction change. Stop the running motor first, because each
            # direction stops through its own command.
            self._disengage()

        self._engage(desired)

    def _engage(self, move: Move) -> None:
        # Claim the move before it reaches the wire. If the command fails half
        # sent, the worker still owes a stop for it.
        self._engaged = move
        self._engaged_unknown = True
        with self._condition:
            self._counters["moves_started"] += 1

        try:
            send_move(self._session(), move.direction, move.step)
        except Exception as error:
            self._record_error(f"move failed: {error}")
            self._drop_session()
            # Never retry a move. The camera moves only when somebody presses,
            # so a failed press becomes a stop.
            with self._condition:
                self._desired = None
            time.sleep(RETRY_SECONDS)

            return

        self._engaged_unknown = False
        logger.info("moving %s at step %d", move.direction, move.step)

    def _disengage(self) -> None:
        engaged = self._engaged
        unknown = self._engaged_unknown

        if engaged is None and not unknown:
            return

        try:
            camera = self._session()
            if engaged is None or unknown:
                # The running direction is not known, so stop every motor.
                send_stop_all(camera)
            else:
                send_stop(camera, engaged.direction, engaged.step)
        except Exception as error:
            self._record_error(f"stop failed: {error}")
            with self._condition:
                self._counters["stop_failures"] += 1
            self._drop_session()
            # The direction is no longer trustworthy, so the retry sweeps all
            # four. The worker loops straight back here because the state is
            # still not settled.
            self._engaged_unknown = True
            time.sleep(RETRY_SECONDS)

            return

        self._engaged = None
        self._engaged_unknown = False
        with self._condition:
            self._counters["stops_completed"] += 1
        self._drop_session()
        logger.info("stopped")

    # --------------------------------------------------------------- session

    def _session(self):
        if self._camera is not None:
            return self._camera

        self._camera = connect()

        return self._camera

    def _drop_session(self) -> None:
        camera = self._camera
        self._camera = None
        if camera is None:
            return

        try:
            camera.close()
        except Exception as error:
            logger.debug("closing the dvrip session failed: %s", error)

    def _record_error(self, message: str) -> None:
        logger.error(message)
        with self._condition:
            self._last_error = message
