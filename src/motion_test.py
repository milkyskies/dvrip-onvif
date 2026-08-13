"""Tests for the motion controller, with a fake camera.

These prove the four safety rules in motion.py without touching the real
camera. Run them with any Python 3.12:

    PYTHONPATH=src:../python-dvr python3 src/motion_test.py

There is no test framework here on purpose. The service itself needs no
package beyond the standard library, and a test that needs one would be the
only reason to install anything.
"""

import sys
import threading
import time

import motion
from motion import MotionController


class FakeCamera:
    """Records every dvrip command, and can be told to fail."""

    def __init__(self, log, failures) -> None:
        self._log = log
        self._failures = failures
        self.closed = False

    def set_command(self, command, data, code=None):
        name = data["Command"]
        preset = data["Parameter"]["Preset"]
        action = "start"
        if preset == -1:
            action = "stop"

        if self._failures.get(action, 0) > 0:
            self._failures[action] -= 1
            self._log.append(f"{action}:{name}:FAIL")
            raise ConnectionError("the camera dropped the command")

        self._log.append(f"{action}:{name}")

    def close(self) -> None:
        self.closed = True


class Harness:
    def __init__(self, failures=None) -> None:
        self.log = []
        self.failures = failures or {}
        self.connects = 0
        self._lock = threading.Lock()

    def connect(self):
        with self._lock:
            self.connects += 1

        return FakeCamera(self.log, self.failures)

    def entries(self):
        with self._lock:
            return list(self.log)


def build(harness, max_move_seconds=1.0) -> MotionController:
    motion.connect = harness.connect
    controller = MotionController(max_move_seconds=max_move_seconds)
    controller.start()

    return controller


def wait_for(predicate, timeout=6.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True

        time.sleep(0.02)

    return False


def check(condition, message) -> None:
    if not condition:
        raise AssertionError(message)


def test_startup_stops_the_camera_before_anything_else() -> None:
    """A restart may have interrupted a move that this process never saw."""
    harness = Harness()
    controller = build(harness)
    check(
        wait_for(lambda: len(harness.entries()) == 4),
        "startup did not sweep all four directions",
    )
    check(
        all(entry.startswith("stop:") for entry in harness.entries()),
        f"startup sent something other than stops: {harness.entries()}",
    )
    controller.shutdown()


def test_a_press_and_a_release_send_one_start_and_one_stop() -> None:
    harness = Harness()
    controller = build(harness)
    wait_for(lambda: len(harness.entries()) == 4)
    harness.log.clear()

    controller.request_move("right", 4, timeout_seconds=60)
    check(
        wait_for(lambda: harness.entries() == ["start:DirectionRight"]),
        f"the move did not reach the camera: {harness.entries()}",
    )

    controller.request_stop()
    check(
        wait_for(
            lambda: harness.entries()
            == ["start:DirectionRight", "stop:DirectionRight"]
        ),
        f"the stop did not reach the camera: {harness.entries()}",
    )
    controller.shutdown()


def test_the_watchdog_stops_a_move_that_nobody_released() -> None:
    """This is the browser tab that closed while an arrow was held."""
    harness = Harness()
    controller = build(harness, max_move_seconds=0.4)
    wait_for(lambda: len(harness.entries()) == 4)
    harness.log.clear()

    controller.request_move("left", 4, timeout_seconds=3600)
    check(
        wait_for(lambda: harness.entries() == ["start:DirectionLeft"]),
        "the move did not reach the camera",
    )

    check(
        wait_for(
            lambda: harness.entries() == ["start:DirectionLeft", "stop:DirectionLeft"],
            timeout=3.0,
        ),
        f"the watchdog did not stop the camera: {harness.entries()}",
    )
    check(
        controller.status().watchdog_stops == 1,
        "the watchdog stop was not counted",
    )
    controller.shutdown()


def test_a_failed_stop_is_retried_and_the_retry_sweeps_every_direction() -> None:
    harness = Harness()
    controller = build(harness)
    wait_for(lambda: len(harness.entries()) == 4)
    harness.log.clear()

    controller.request_move("up", 4, timeout_seconds=60)
    wait_for(lambda: harness.entries() == ["start:DirectionUp"])

    # The next stop fails on the wire. The direction is no longer trustworthy,
    # so the retry must stop every motor rather than only the one it started.
    harness.failures["stop"] = 1
    controller.request_stop()

    check(
        wait_for(
            lambda: harness.entries()
            == [
                "start:DirectionUp",
                "stop:DirectionUp:FAIL",
                "stop:DirectionUp",
                "stop:DirectionDown",
                "stop:DirectionLeft",
                "stop:DirectionRight",
            ],
            timeout=5.0,
        ),
        f"the failed stop was not retried as a full sweep: {harness.entries()}",
    )
    check(controller.status().stop_failures == 1, "the stop failure was not counted")
    controller.shutdown()


def test_a_move_that_fails_is_still_followed_by_a_stop() -> None:
    """A half sent start may still have moved a motor, so it must be stopped."""
    harness = Harness()
    controller = build(harness)
    wait_for(lambda: len(harness.entries()) == 4)
    harness.log.clear()

    harness.failures["start"] = 1
    controller.request_move("down", 4, timeout_seconds=60)

    check(
        wait_for(
            lambda: harness.entries()
            == [
                "start:DirectionDown:FAIL",
                "stop:DirectionUp",
                "stop:DirectionDown",
                "stop:DirectionLeft",
                "stop:DirectionRight",
            ],
            timeout=5.0,
        ),
        f"a failed move was not followed by a stop: {harness.entries()}",
    )
    controller.shutdown()


def test_a_move_is_never_retried_by_itself() -> None:
    """The camera moves when somebody presses, and at no other time."""
    harness = Harness()
    controller = build(harness)
    wait_for(lambda: len(harness.entries()) == 4)

    controller.request_move("right", 4, timeout_seconds=0.3)
    wait_for(lambda: controller.status().watchdog_stops == 1, timeout=3.0)

    settled = len(harness.entries())
    time.sleep(1.5)
    check(
        len(harness.entries()) == settled,
        f"the controller talked to the camera while idle: {harness.entries()[settled:]}",
    )
    check(
        controller.status().moves_started == 1,
        "more moves were started than were asked for",
    )
    controller.shutdown()


def test_shutdown_stops_the_camera() -> None:
    harness = Harness()
    controller = build(harness)
    wait_for(lambda: len(harness.entries()) == 4)
    harness.log.clear()

    controller.request_move("left", 4, timeout_seconds=60)
    wait_for(lambda: harness.entries() == ["start:DirectionLeft"])

    controller.shutdown()
    check(
        harness.entries() == ["start:DirectionLeft", "stop:DirectionLeft"],
        f"shutdown left the camera moving: {harness.entries()}",
    )


def main() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as error:
            failures += 1
            print(f"FAIL {test.__name__}\n     {error}")
        else:
            print(f"ok   {test.__name__}")

    print(f"\n{len(tests) - failures}/{len(tests)} passed")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
