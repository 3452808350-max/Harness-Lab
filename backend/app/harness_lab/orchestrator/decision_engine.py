"""Continue vs Spawn Decision Engine.

This module implements Claude Plugin's decision logic for choosing
whether a worker should continue with existing context or spawn
a fresh worker with clean context.

Design source: Claude Plugin Module 05 - Coordinator Mode
See: design/claude-plugin-integration-design.md Section 2.1.3

Decision scenes (evaluated in order):
1. Worker has files in context → Continue
2. Broad research, narrow implementation → Spawn
3. Correcting failed attempt → Continue
4. Verifying code just written → Spawn (fresh eyes)
5. Wrong approach retry → Spawn
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types.decision import TaskContext, ContinueVsSpawnDecision

if TYPE_CHECKING:
    pass


class ContinueSpawnDecisionEngine:
    """Engine for deciding Continue vs Spawn.
    
    Implements the 5 decision scenes from Claude Plugin's
    Continue vs Spawn decision logic.
    
    Usage:
        engine = ContinueSpawnDecisionEngine()
        context = TaskContext(
            research_files=['src/main.py'],
            target_files=['src/main.py'],
        )
        decision = engine.decide(context)
        if decision.should_continue():
            # Use existing worker
        else:
            # Spawn fresh worker
    """
    
    # Scene priority (higher = evaluated first)
    SCENE_PRIORITIES = {
        "files_in_context": 100,
        "scope_mismatch": 80,
        "error_correction": 70,
        "fresh_eyes": 60,
        "wrong_approach": 50,
    }
    
    # Human-readable reasons for each scene
    SCENE_REASONS = {
        "files_in_context": "Worker already has target files in context from research phase",
        "scope_mismatch": "Avoid exploration noise from broad research, focused context is cleaner for narrow implementation",
        "error_correction": "Worker has useful error context for debugging failed attempt",
        "fresh_eyes": "Verifier should have fresh perspective without implementation bias",
        "wrong_approach": "Wrong-approach context pollutes retry, fresh start needed",
        "default_spawn": "No useful context to reuse, spawning fresh worker",
        "default_continue": "Context available and relevant, continuing with existing worker",
    }
    
    def decide(self, context: TaskContext) -> ContinueVsSpawnDecision:
        """Evaluate scenes in order and return decision.
        
        Scenes are evaluated in priority order. First matching scene
        determines the decision. If no scene matches, default is spawn.
        
        Args:
            context: TaskContext with decision parameters
            
        Returns:
            ContinueVsSpawnDecision with action, reason, and confidence
        """
        # Evaluate scenes in priority order
        decisions = []
        
        # Scene 1: Worker has files in context
        if context.worker_has_research_files_in_target():
            decisions.append(
                ContinueVsSpawnDecision(
                    action="continue",
                    reason=self.SCENE_REASONS["files_in_context"],
                    confidence=0.95,
                )
            )
        
        # Scene 2: Broad research, narrow implementation
        if context.has_scope_mismatch():
            decisions.append(
                ContinueVsSpawnDecision(
                    action="spawn",
                    reason=self.SCENE_REASONS["scope_mismatch"],
                    confidence=0.85,
                )
            )
        
        # Scene 3: Correcting failed attempt
        if context.is_error_correction():
            decisions.append(
                ContinueVsSpawnDecision(
                    action="continue",
                    reason=self.SCENE_REASONS["error_correction"],
                    confidence=0.90,
                )
            )
        
        # Scene 4: Verifying code just written (needs fresh eyes)
        if context.needs_fresh_eyes():
            decisions.append(
                ContinueVsSpawnDecision(
                    action="spawn",
                    reason=self.SCENE_REASONS["fresh_eyes"],
                    confidence=0.85,
                )
            )
        
        # Scene 5: Wrong approach retry
        if context.has_wrong_approach_context():
            decisions.append(
                ContinueVsSpawnDecision(
                    action="spawn",
                    reason=self.SCENE_REASONS["wrong_approach"],
                    confidence=0.90,
                )
            )
        
        # Return highest priority decision
        if decisions:
            # Sort by confidence and return best
            return max(decisions, key=lambda d: d.confidence)
        
        # Default: spawn fresh worker
        return ContinueVsSpawnDecision(
            action="spawn",
            reason=self.SCENE_REASONS["default_spawn"],
            confidence=0.5,
        )
    
    def decide_with_reasoning(self, context: TaskContext) -> dict:
        """Return decision with full reasoning trace.
        
        Useful for debugging and logging decisions.
        
        Args:
            context: TaskContext with decision parameters
            
        Returns:
            dict with decision, reasoning, and all evaluated scenes
        """
        scenes_evaluated = []
        
        # Evaluate each scene
        scenes_evaluated.append({
            "scene": "files_in_context",
            "matched": context.worker_has_research_files_in_target(),
            "action_if_matched": "continue",
        })
        
        scenes_evaluated.append({
            "scene": "scope_mismatch",
            "matched": context.has_scope_mismatch(),
            "action_if_matched": "spawn",
        })
        
        scenes_evaluated.append({
            "scene": "error_correction",
            "matched": context.is_error_correction(),
            "action_if_matched": "continue",
        })
        
        scenes_evaluated.append({
            "scene": "fresh_eyes",
            "matched": context.needs_fresh_eyes(),
            "action_if_matched": "spawn",
        })
        
        scenes_evaluated.append({
            "scene": "wrong_approach",
            "matched": context.has_wrong_approach_context(),
            "action_if_matched": "spawn",
        })
        
        decision = self.decide(context)
        
        return {
            "decision": decision.model_dump(),
            "scenes_evaluated": scenes_evaluated,
            "matched_scenes": [s["scene"] for s in scenes_evaluated if s["matched"]],
        }


# Convenience function for quick decisions
def should_continue_or_spawn(
    research_files: list[str] = None,
    target_files: list[str] = None,
    research_scope: str = "broad",
    impl_scope: str = "narrow",
    is_retry: bool = False,
    previous_attempt_failed: bool = False,
    is_verification: bool = False,
    target_worker_just_wrote_code: bool = False,
    previous_approach_was_wrong: bool = False,
) -> ContinueVsSpawnDecision:
    """Quick decision function for Continue vs Spawn.
    
    Convenience wrapper for simple use cases.
    
    Args:
        research_files: Files worker already researched
        target_files: Files worker needs to work on
        research_scope: 'broad' or 'narrow'
        impl_scope: 'broad' or 'narrow'
        is_retry: Is this a retry attempt
        previous_attempt_failed: Did previous attempt fail
        is_verification: Is this a verification task
        target_worker_just_wrote_code: Did same worker write code being verified
        previous_approach_was_wrong: Was previous approach fundamentally wrong
        
    Returns:
        ContinueVsSpawnDecision
    """
    engine = ContinueSpawnDecisionEngine()
    context = TaskContext(
        research_files=research_files or [],
        target_files=target_files or [],
        research_scope=research_scope,
        impl_scope=impl_scope,
        is_retry=is_retry,
        previous_attempt_failed=previous_attempt_failed,
        is_verification=is_verification,
        target_worker_just_wrote_code=target_worker_just_wrote_code,
        previous_approach_was_wrong=previous_approach_was_wrong,
    )
    return engine.decide(context)