"""Decision engine types for Continue vs Spawn decisions.

Design source: Claude Plugin Module 05 - Coordinator Mode
See: design/claude-plugin-integration-design.md
"""

from typing import List

from pydantic import BaseModel, Field


class TaskContext(BaseModel):
    """Context for Continue vs Spawn decision.
    
    Captures the state needed to decide whether a worker should
    continue with current context or spawn a fresh worker.
    
    Decision scenes from Claude Plugin:
    1. Worker has files in context → Continue
    2. Broad research, narrow implementation → Spawn
    3. Correcting failed attempt → Continue
    4. Verifying code just written → Spawn (fresh eyes)
    5. Wrong approach retry → Spawn
    """
    research_files: List[str] = Field(default_factory=list)
    target_files: List[str] = Field(default_factory=list)
    research_scope: str = "broad"  # 'broad' | 'narrow'
    impl_scope: str = "narrow"  # 'broad' | 'narrow'
    is_retry: bool = False
    previous_attempt_failed: bool = False
    is_verification: bool = False
    target_worker_just_wrote_code: bool = False
    previous_approach_was_wrong: bool = False
    
    def worker_has_research_files_in_target(self) -> bool:
        """Check if worker has relevant files already in context.
        
        Scene 1: Worker has files in context → Continue
        """
        if not self.research_files or not self.target_files:
            return False
        # Check if any target file was already researched
        target_set = set(self.target_files)
        research_set = set(self.research_files)
        return bool(target_set & research_set)
    
    def has_scope_mismatch(self) -> bool:
        """Check if research scope doesn't match implementation scope.
        
        Scene 2: Broad research, narrow implementation → Spawn
        """
        return self.research_scope == "broad" and self.impl_scope == "narrow"
    
    def is_error_correction(self) -> bool:
        """Check if this is a retry with useful error context.
        
        Scene 3: Correcting failed attempt → Continue
        """
        return self.is_retry and self.previous_attempt_failed
    
    def needs_fresh_eyes(self) -> bool:
        """Check if verification needs fresh perspective.
        
        Scene 4: Verifying code just written → Spawn
        """
        return self.is_verification and self.target_worker_just_wrote_code
    
    def has_wrong_approach_context(self) -> bool:
        """Check if previous approach was wrong.
        
        Scene 5: Wrong approach retry → Spawn
        """
        return self.is_retry and self.previous_approach_was_wrong


class ContinueVsSpawnDecision(BaseModel):
    """Decision result for Continue vs Spawn.
    
    action: 'continue' - reuse existing worker context
            'spawn' - create fresh worker with clean context
    
    reason: Human-readable explanation of the decision
    
    confidence: 0.0-1.0 confidence level (default 1.0)
    """
    action: str  # 'continue' | 'spawn'
    reason: str
    confidence: float = 1.0
    
    def should_continue(self) -> bool:
        """Check if decision is to continue."""
        return self.action == "continue"
    
    def should_spawn(self) -> bool:
        """Check if decision is to spawn new worker."""
        return self.action == "spawn"