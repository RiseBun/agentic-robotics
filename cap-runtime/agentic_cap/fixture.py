"""Deterministic protocol test backend. It is never a robot success benchmark."""
import threading
import time

from .core import Halt


class FixtureBackend:
    period = .01

    def __init__(self, episode):
        self.episode = episode
        self.q = [0.] * 7
        self.ticks = 0
        self.owner = None
        self.held = None
        self.placed = None
        self.occluded = False
        self.reset_count = 0

    def initialize(self):
        self.owner = threading.get_ident()
        self.reset_count += 1

    def joints(self):
        assert threading.get_ident() == self.owner
        return self.q.copy()

    def step(self, target, gripper):
        assert threading.get_ident() == self.owner
        self.q = [a + max(-.01, min(.01, b-a)) for a, b in zip(self.q, target)]
        self.ticks += 1

    def sim_time(self):
        return self.ticks * self.period

    def capture(self):
        return dict(joint_positions=self.q.copy(), gripper_joint_positions=[.04, .04],
                    evidence_ref='fixture', objects={}, backend='fixture')

    def tools(self, segment):
        def wait_motion(steps=20):
            if not isinstance(steps, int) or not 1 <= steps <= 200:
                raise ValueError('Invalid steps')
            e = self.episode
            with e.cv:
                e.motion = True
                end = self.ticks + steps
                while self.ticks < end:
                    e.checkpoint(segment)
                    e.cv.wait(.02)
                e.motion = False
            return {'status': 'verified'}

        def pick(name, miss=False):
            wait_motion()
            if miss:
                return dict(status='failed', reason='fixture missed grasp')
            self.held = name
            return dict(status='verified')

        def place(name):
            wait_motion()
            self.placed, self.held = [self.held, name], None
            return dict(status='verified')

        return dict(wait_motion=wait_motion, pick=pick, place_on=place,
                    get_execution_state=lambda: self.episode.observe())

    def verify(self, goal, snapshot):
        if self.occluded:
            return dict(status='unknown', reason='fixture occlusion')
        ok = ('holding' not in goal or self.held == goal['holding']) and ('on' not in goal or self.placed == goal['on']) and (not goal.get('empty_gripper') or self.held is None)
        return dict(status='verified' if ok else 'failed', reason='fixture assertion only')

    def close(self):
        pass
