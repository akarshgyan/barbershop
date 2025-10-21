#!/usr/bin/env python3
# Sleeping Barber Problem — Terminal Animation (MacOS friendly)
# - Uses mutex + semaphores for synchronization
# - Producer/Consumer: customer generator produces customers; barber consumes
# - Curses UI with keyboard controls: 1–5 to change arrival speed, q to quit

import curses
import time
import threading
import itertools
from collections import deque
from typing import Deque, Optional


class ShopState:
    def __init__(self, capacity: int = 5, cut_time: float = 1.6) -> None:
        # Configuration
        self.capacity: int = capacity
        self.cut_time: float = cut_time
        self.arrival_speed: int = 3  # 1 (slow) .. 5 (fast)

        # Dynamic state (protected by lock)
        self.waiting: Deque[int] = deque()
        self.served: int = 0
        self.left: int = 0
        self.barber_status: str = "sleeping"  # sleeping | cutting
        self.current_customer: Optional[int] = None
        self.haircut_start_time: Optional[float] = None

        # Synchronization
        self.lock = threading.Lock()


# Semaphores for the classical solution
customers_sem = threading.Semaphore(0)  # counts waiting customers
barber_sem = threading.Semaphore(0)     # signals a barber is ready


def barber_worker(state: ShopState, stop_event: threading.Event) -> None:
    """Barber thread: sleeps when no customers; cuts hair when available."""
    while not stop_event.is_set():
        # Wait for a customer (with timeout to allow responsive shutdown)
        acquired = customers_sem.acquire(timeout=0.1)
        if not acquired:
            # still no customers — reflect sleeping status if appropriate
            with state.lock:
                if not state.waiting and state.barber_status != "cutting":
                    state.barber_status = "sleeping"
            continue

        # A customer is available; move them from waiting to the chair
        with state.lock:
            if state.waiting:
                cid = state.waiting.popleft()
                state.barber_status = "cutting"
                state.current_customer = cid
                state.haircut_start_time = time.monotonic()
                # Signal customer to sit in the barber chair
                barber_sem.release()
            else:
                # Spurious wake; continue
                continue

        # Simulate cutting hair in small slices to keep UI responsive
        remaining = state.cut_time
        step = 0.05
        while remaining > 0 and not stop_event.is_set():
            time.sleep(step)
            remaining -= step

        # Finish haircut
        with state.lock:
            if state.current_customer == cid:
                state.current_customer = None
                state.haircut_start_time = None
                state.served += 1
                # If nobody's waiting, barber sleeps; else next loop will pick next
                state.barber_status = "sleeping" if not state.waiting else "cutting"

    # graceful exit


def customer_worker(state: ShopState, cid: int, stop_event: threading.Event) -> None:
    """Customer thread: either waits, gets served, or leaves if full."""
    admitted = False

    with state.lock:
        if len(state.waiting) < state.capacity:
            state.waiting.append(cid)
            admitted = True
        else:
            state.left += 1
            admitted = False

    if not admitted:
        return

    # Notify barber that a customer is waiting
    customers_sem.release()

    # Wait until the barber is ready for this customer, with periodic checks for shutdown
    while not stop_event.is_set():
        if barber_sem.acquire(timeout=0.1):
            break

    # After acquiring barber_sem, the barber handles the haircut simulation.


def generator_worker(state: ShopState, stop_event: threading.Event) -> None:
    """Generates customers according to current speed setting."""
    speed_to_interval = {1: 2.0, 2: 1.0, 3: 0.6, 4: 0.4, 5: 0.25}
    counter = itertools.count(1)

    # Pace loop without drifting too much
    next_emit = time.monotonic()
    while not stop_event.is_set():
        with state.lock:
            speed = state.arrival_speed
        interval = speed_to_interval.get(speed, 0.6)

        now = time.monotonic()
        if now < next_emit:
            time.sleep(min(0.05, next_emit - now))
            continue

        cid = next(counter)
        t = threading.Thread(target=customer_worker, args=(state, cid, stop_event), daemon=True)
        t.start()

        next_emit = now + interval


def draw_ui(stdscr, state: ShopState) -> None:
    stdscr.erase()

    # Snapshot the state for consistent render
    with state.lock:
        capacity = state.capacity
        waiting_list = list(state.waiting)
        served = state.served
        left = state.left
        barber_status = state.barber_status
        current = state.current_customer
        cut_start = state.haircut_start_time
        cut_time = state.cut_time
        speed = state.arrival_speed

    # Title
    stdscr.addstr(0, 2, "💈 Sleeping Barber — Barbershop Simulation")
    stdscr.addstr(1, 2, "Controls: [1–5]=arrival speed, q=quit")

    # Stats
    stdscr.addstr(3, 2, f"Chairs: {capacity}  |  Speed: {speed}  |  Served: {served}  |  Left: {left}")

    # Barber status and progress bar
    y = 5
    if barber_status == "cutting" and current is not None and cut_start is not None:
        elapsed = max(0.0, time.monotonic() - cut_start)
        prog = min(1.0, elapsed / max(0.001, cut_time))
        bar_w = 24
        filled = int(prog * bar_w)
        bar = "#" * filled + "-" * (bar_w - filled)
        stdscr.addstr(y, 2, f"Barber: Cutting C{current:03d}  [{bar}] {int(prog*100):3d}%")
    else:
        stdscr.addstr(y, 2, "Barber: Zzz (sleeping)")

    # Waiting chairs visualization
    y += 2
    stdscr.addstr(y, 2, "Waiting chairs:")
    y += 1
    chairs_line = []
    for i in range(capacity):
        if i < len(waiting_list):
            cid = waiting_list[i]
            chairs_line.append(f"[{cid:02d}]")
        else:
            chairs_line.append("[  ]")
    stdscr.addstr(y, 4, " ".join(chairs_line))

    # Legend
    y += 2
    stdscr.addstr(y, 2, "Legend: [NN]=customer id in queue; empty chair=[  ]")

    stdscr.refresh()


def run_curses(state: ShopState) -> None:
    stop_event = threading.Event()
    barber_t = threading.Thread(target=barber_worker, args=(state, stop_event), daemon=True)
    gen_t = threading.Thread(target=generator_worker, args=(state, stop_event), daemon=True)
    barber_t.start()
    gen_t.start()

    def wrapped(stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)
        stdscr.timeout(100)  # 100ms

        while True:
            # Handle input
            try:
                ch = stdscr.getch()
            except Exception:
                ch = -1

            if ch in (ord('q'), ord('Q')):
                break
            elif ch in (ord('1'), ord('2'), ord('3'), ord('4'), ord('5')):
                with state.lock:
                    state.arrival_speed = int(chr(ch))

            # Draw
            draw_ui(stdscr, state)

        # Exit loop — request threads to stop
        stop_event.set()

    curses.wrapper(wrapped)

    # Give threads a moment to wind down gracefully
    barber_t.join(timeout=1.0)
    gen_t.join(timeout=1.0)


def main() -> None:
    # Default: 5 chairs, 1.6s haircut time
    state = ShopState(capacity=5, cut_time=1.6)
    run_curses(state)


if __name__ == "__main__":
    main()
