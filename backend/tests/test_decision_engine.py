"""Tests for ContinueSpawnDecisionEngine.

Design source: Claude Plugin Module 05 - Coordinator Mode
"""

import pytest

from harness_lab.types.decision import TaskContext, ContinueVsSpawnDecision
from harness_lab.orchestrator.decision_engine import (
    ContinueSpawnDecisionEngine,
    should_continue_or_spawn,
)


class TestTaskContext:
    """Tests for TaskContext helper methods."""
    
    def test_worker_has_files_in_context_true(self):
        """Scene 1: Worker has target files in research context."""
        context = TaskContext(
            research_files=["src/main.py", "src/utils.py"],
            target_files=["src/main.py"],
        )
        assert context.worker_has_research_files_in_target() is True
    
    def test_worker_has_files_in_context_false(self):
        """No overlapping files."""
        context = TaskContext(
            research_files=["src/old.py"],
            target_files=["src/new.py"],
        )
        assert context.worker_has_research_files_in_target() is False
    
    def test_has_scope_mismatch_true(self):
        """Scene 2: Broad research, narrow implementation."""
        context = TaskContext(
            research_scope="broad",
            impl_scope="narrow",
        )
        assert context.has_scope_mismatch() is True
    
    def test_has_scope_mismatch_false(self):
        """Same scope."""
        context = TaskContext(
            research_scope="narrow",
            impl_scope="narrow",
        )
        assert context.has_scope_mismatch() is False
    
    def test_is_error_correction_true(self):
        """Scene 3: Retry with failed attempt."""
        context = TaskContext(
            is_retry=True,
            previous_attempt_failed=True,
        )
        assert context.is_error_correction() is True
    
    def test_is_error_correction_false_not_retry(self):
        """Not a retry."""
        context = TaskContext(
            is_retry=False,
            previous_attempt_failed=True,
        )
        assert context.is_error_correction() is False
    
    def test_needs_fresh_eyes_true(self):
        """Scene 4: Verification after same worker wrote code."""
        context = TaskContext(
            is_verification=True,
            target_worker_just_wrote_code=True,
        )
        assert context.needs_fresh_eyes() is True
    
    def test_needs_fresh_eyes_false(self):
        """Verification by different worker."""
        context = TaskContext(
            is_verification=True,
            target_worker_just_wrote_code=False,
        )
        assert context.needs_fresh_eyes() is False
    
    def test_has_wrong_approach_context_true(self):
        """Scene 5: Wrong approach retry."""
        context = TaskContext(
            is_retry=True,
            previous_approach_was_wrong=True,
        )
        assert context.has_wrong_approach_context() is True


class TestContinueVsSpawnDecision:
    """Tests for ContinueVsSpawnDecision."""
    
    def test_should_continue(self):
        decision = ContinueVsSpawnDecision(
            action="continue",
            reason="Worker has context",
        )
        assert decision.should_continue() is True
        assert decision.should_spawn() is False
    
    def test_should_spawn(self):
        decision = ContinueVsSpawnDecision(
            action="spawn",
            reason="Fresh eyes needed",
        )
        assert decision.should_spawn() is True
        assert decision.should_continue() is False
    
    def test_default_confidence(self):
        decision = ContinueVsSpawnDecision(
            action="continue",
            reason="Test",
        )
        assert decision.confidence == 1.0


class TestContinueSpawnDecisionEngine:
    """Tests for decision engine scenes."""
    
    def test_scene1_continue_with_files_in_context(self):
        """Scene 1: Continue when worker has target files."""
        engine = ContinueSpawnDecisionEngine()
        context = TaskContext(
            research_files=["src/main.py"],
            target_files=["src/main.py"],
        )
        decision = engine.decide(context)
        assert decision.should_continue() is True
        assert "files in context" in decision.reason.lower()
    
    def test_scene2_spawn_for_scope_mismatch(self):
        """Scene 2: Spawn for broad->narrow scope change."""
        engine = ContinueSpawnDecisionEngine()
        # Explicitly set ONLY scope mismatch conditions
        context = TaskContext(
            research_scope="broad",
            impl_scope="narrow",
            is_retry=False,  # Don't trigger error_correction
            previous_attempt_failed=False,
            is_verification=False,  # Don't trigger fresh_eyes
            target_worker_just_wrote_code=False,
            previous_approach_was_wrong=False,
        )
        decision = engine.decide(context)
        assert decision.should_spawn() is True
        # Check for keywords that appear in the scope mismatch reason
        assert "noise" in decision.reason.lower() or "focused" in decision.reason.lower() or "scope" in decision.reason.lower()
    
    def test_scene3_continue_for_error_correction(self):
        """Scene 3: Continue to use error context."""
        engine = ContinueSpawnDecisionEngine()
        # Set ONLY error_correction conditions, disable scope_mismatch
        context = TaskContext(
            is_retry=True,
            previous_attempt_failed=True,
            research_scope="narrow",  # Same scope to avoid triggering scope_mismatch
            impl_scope="narrow",
            is_verification=False,  # Don't trigger fresh_eyes
            target_worker_just_wrote_code=False,
            previous_approach_was_wrong=False,  # Don't trigger wrong_approach
        )
        decision = engine.decide(context)
        assert decision.should_continue() is True
        assert "error" in decision.reason.lower() or "failed" in decision.reason.lower() or "debugging" in decision.reason.lower()
    
    def test_scene4_spawn_for_fresh_eyes(self):
        """Scene 4: Spawn for verification."""
        engine = ContinueSpawnDecisionEngine()
        # Set ONLY fresh_eyes conditions, disable scope_mismatch
        context = TaskContext(
            is_verification=True,
            target_worker_just_wrote_code=True,
            research_scope="narrow",  # Same scope to avoid triggering scope_mismatch
            impl_scope="narrow",
            is_retry=False,  # Don't trigger error_correction
            previous_attempt_failed=False,
            previous_approach_was_wrong=False,
        )
        decision = engine.decide(context)
        assert decision.should_spawn() is True
        assert "fresh" in decision.reason.lower() or "verifier" in decision.reason.lower() or "bias" in decision.reason.lower()
    
    def test_scene5_spawn_for_wrong_approach(self):
        """Scene 5: Spawn when approach was wrong."""
        engine = ContinueSpawnDecisionEngine()
        # Set ONLY wrong_approach conditions, disable other scenes
        context = TaskContext(
            is_retry=True,
            previous_approach_was_wrong=True,
            previous_attempt_failed=False,  # Don't trigger error_correction
            research_scope="narrow",  # Same scope to avoid triggering scope_mismatch
            impl_scope="narrow",
            is_verification=False,  # Don't trigger fresh_eyes
            target_worker_just_wrote_code=False,
        )
        decision = engine.decide(context)
        assert decision.should_spawn() is True
        assert "wrong" in decision.reason.lower() or "approach" in decision.reason.lower() or "fresh start" in decision.reason.lower()
    
    def test_default_spawn_when_no_context(self):
        """Default: Spawn when no useful context."""
        engine = ContinueSpawnDecisionEngine()
        # Set all scene conditions to False
        context = TaskContext(
            research_scope="narrow",  # Same scope to avoid triggering scope_mismatch
            impl_scope="narrow",
            is_retry=False,
            previous_attempt_failed=False,
            is_verification=False,
            target_worker_just_wrote_code=False,
            previous_approach_was_wrong=False,
        )
        decision = engine.decide(context)
        assert decision.should_spawn() is True
    
    def test_priority_files_in_context_over_scope_mismatch(self):
        """Files in context should take priority over scope mismatch."""
        engine = ContinueSpawnDecisionEngine()
        context = TaskContext(
            research_files=["src/main.py"],
            target_files=["src/main.py"],
            research_scope="broad",
            impl_scope="narrow",
        )
        decision = engine.decide(context)
        # Files in context has higher confidence
        assert decision.confidence >= 0.9
    
    def test_decide_with_reasoning(self):
        """decide_with_reasoning returns full trace."""
        engine = ContinueSpawnDecisionEngine()
        context = TaskContext(
            research_files=["src/main.py"],
            target_files=["src/main.py"],
        )
        result = engine.decide_with_reasoning(context)
        
        assert "decision" in result
        assert "scenes_evaluated" in result
        assert "matched_scenes" in result
        
        assert len(result["scenes_evaluated"]) == 5
        assert "files_in_context" in result["matched_scenes"]


class TestShouldContinueOrSpawn:
    """Tests for convenience function."""
    
    def test_simple_continue(self):
        decision = should_continue_or_spawn(
            research_files=["src/main.py"],
            target_files=["src/main.py"],
        )
        assert decision.should_continue() is True
    
    def test_simple_spawn(self):
        decision = should_continue_or_spawn(
            research_scope="broad",
            impl_scope="narrow",
        )
        assert decision.should_spawn() is True