"""Token Budget Allocator for multi-agent orchestration.

Design source: Claude Plugin Module 05 - Coordinator Mode
Token Budget Enforcement: Section 2.1.2

Key features:
    - Phase-specific budget allocation
    - Worker-level budget tracking
    - Budget exhaustion handling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum


class BudgetExhaustionPolicy(Enum):
    """Policy for handling budget exhaustion."""
    FAIL_FAST = "fail_fast"  # Stop execution immediately
    GRACEFUL = "graceful"  # Allow current phase to finish, skip remaining
    BEST_EFFORT = "best_effort"  # Continue with reduced budget per worker


@dataclass
class TokenBudgetConfig:
    """Configuration for token budget allocation.
    
    Total budget is distributed across phases:
    - Research: 25% (multiple workers)
    - Synthesis: 10% (coordinator only)
    - Implementation: 40% (execution workers)
    - Verification: 25% (validation workers)
    """
    total_budget: int = 100000  # Default 100k tokens
    phase_allocation: Dict[str, float] = field(default_factory=lambda: {
        "research": 0.25,
        "synthesis": 0.10,
        "implementation": 0.40,
        "verification": 0.25,
    })
    worker_buffer: int = 5000  # Buffer tokens per worker for safety
    exhaustion_policy: BudgetExhaustionPolicy = BudgetExhaustionPolicy.GRACEFUL
    warn_threshold: float = 0.80  # Warn at 80% usage


@dataclass
class PhaseBudget:
    """Budget allocation for a specific phase."""
    phase_name: str
    allocated: int
    used: int = 0
    workers: Dict[str, int] = field(default_factory=dict)  # worker_id -> used
    
    def remaining(self) -> int:
        """Get remaining budget for phase."""
        return self.allocated - self.used
    
    def percentage_used(self) -> float:
        """Get percentage of budget used."""
        if self.allocated == 0:
            return 0.0
        return self.used / self.allocated
    
    def can_allocate(self, amount: int) -> bool:
        """Check if amount can be allocated."""
        return self.remaining() >= amount
    
    def allocate(self, worker_id: str, amount: int) -> bool:
        """Allocate budget to worker.
        
        Args:
            worker_id: Worker ID
            amount: Amount to allocate
            
        Returns:
            True if allocation successful, False if insufficient budget
        """
        if not self.can_allocate(amount):
            return False
        
        self.workers[worker_id] = self.workers.get(worker_id, 0) + amount
        self.used += amount
        return True
    
    def release(self, worker_id: str) -> int:
        """Release unused budget from worker.
        
        Args:
            worker_id: Worker ID
            
        Returns:
            Amount released
        """
        if worker_id not in self.workers:
            return 0
        
        # In practice, we'd track actual vs allocated usage
        # For now, assume all allocated was used
        used = self.workers.pop(worker_id, 0)
        return used


@dataclass
class BudgetStatus:
    """Overall budget status."""
    total_budget: int
    total_used: int
    phases: Dict[str, PhaseBudget]
    
    def remaining(self) -> int:
        """Get remaining budget."""
        return self.total_budget - self.total_used
    
    def percentage_used(self) -> float:
        """Get percentage of total budget used."""
        if self.total_budget == 0:
            return 0.0
        return self.total_used / self.total_budget
    
    def get_phase_budget(self, phase: str) -> Optional[PhaseBudget]:
        """Get budget for specific phase."""
        return self.phases.get(phase)
    
    def is_exhausted(self) -> bool:
        """Check if budget is exhausted."""
        return self.remaining() <= 0


class TokenBudgetAllocator:
    """Allocates and tracks token budgets across phases and workers.
    
    Usage:
        allocator = TokenBudgetAllocator(config)
        
        # Check budget before worker execution
        if allocator.can_allocate("implementation", worker_id, 10000):
            allocator.allocate("implementation", worker_id, 10000)
            # Execute worker...
            allocator.record_usage(worker_id, actual_used)
    """
    
    def __init__(self, config: TokenBudgetConfig = None) -> None:
        """Initialize budget allocator.
        
        Args:
            config: TokenBudgetConfig (optional, defaults provided)
        """
        self.config = config or TokenBudgetConfig()
        self.status: Optional[BudgetStatus] = None
        self._initialize_budget()
    
    def _initialize_budget(self) -> None:
        """Initialize budget status with phase allocations."""
        phases = {}
        total_allocated = 0
        
        for phase_name, allocation_pct in self.config.phase_allocation.items():
            allocated = int(self.config.total_budget * allocation_pct)
            phases[phase_name] = PhaseBudget(
                phase_name=phase_name,
                allocated=allocated,
            )
            total_allocated += allocated
        
        self.status = BudgetStatus(
            total_budget=self.config.total_budget,
            total_used=0,
            phases=phases,
        )
    
    def can_allocate(self, phase: str, worker_id: str, amount: int) -> bool:
        """Check if budget can be allocated to worker.
        
        Args:
            phase: Phase name
            worker_id: Worker ID
            amount: Amount to allocate
            
        Returns:
            True if budget available
        """
        if not self.status:
            return False
        
        phase_budget = self.status.get_phase_budget(phase)
        if not phase_budget:
            return False
        
        # Add buffer for safety
        effective_amount = amount + self.config.worker_buffer
        
        return phase_budget.can_allocate(effective_amount)
    
    def allocate(self, phase: str, worker_id: str, amount: int) -> bool:
        """Allocate budget to worker.
        
        Args:
            phase: Phase name
            worker_id: Worker ID
            amount: Amount to allocate
            
        Returns:
            True if allocation successful
        """
        if not self.can_allocate(phase, worker_id, amount):
            return False
        
        phase_budget = self.status.get_phase_budget(phase)
        if not phase_budget:
            return False
        
        effective_amount = amount + self.config.worker_buffer
        
        success = phase_budget.allocate(worker_id, effective_amount)
        if success:
            self.status.total_used += effective_amount
        
        return success
    
    def record_usage(self, phase: str, worker_id: str, actual_used: int) -> None:
        """Record actual token usage after worker execution.
        
        Adjusts budget based on actual vs allocated usage.
        
        Args:
            phase: Phase name
            worker_id: Worker ID
            actual_used: Actual tokens used
        """
        if not self.status:
            return
        
        phase_budget = self.status.get_phase_budget(phase)
        if not phase_budget or worker_id not in phase_budget.workers:
            return
        
        allocated = phase_budget.workers[worker_id]
        
        # If actual usage is less than allocated, release difference
        if actual_used < allocated:
            difference = allocated - actual_used
            phase_budget.used -= difference
            self.status.total_used -= difference
            phase_budget.workers[worker_id] = actual_used
    
    def release_unused(self, phase: str, worker_id: str) -> int:
        """Release unused budget from completed worker.
        
        Args:
            phase: Phase name
            worker_id: Worker ID
            
        Returns:
            Amount released
        """
        if not self.status:
            return 0
        
        phase_budget = self.status.get_phase_budget(phase)
        if not phase_budget:
            return 0
        
        released = phase_budget.release(worker_id)
        self.status.total_used -= released
        
        return released
    
    def get_status(self) -> Optional[BudgetStatus]:
        """Get current budget status.
        
        Returns:
            BudgetStatus or None
        """
        return self.status
    
    def get_phase_remaining(self, phase: str) -> int:
        """Get remaining budget for phase.
        
        Args:
            phase: Phase name
            
        Returns:
            Remaining tokens
        """
        if not self.status:
            return 0
        
        phase_budget = self.status.get_phase_budget(phase)
        if not phase_budget:
            return 0
        
        return phase_budget.remaining()
    
    def is_phase_exhausted(self, phase: str) -> bool:
        """Check if phase budget is exhausted.
        
        Args:
            phase: Phase name
            
        Returns:
            True if exhausted
        """
        return self.get_phase_remaining(phase) <= 0
    
    def should_warn(self) -> bool:
        """Check if budget usage exceeds warning threshold.
        
        Returns:
            True if should warn
        """
        if not self.status:
            return False
        
        return self.status.percentage_used() >= self.config.warn_threshold
    
    def handle_exhaustion(self) -> BudgetExhaustionPolicy:
        """Get policy for handling budget exhaustion.
        
        Returns:
            BudgetExhaustionPolicy
        """
        return self.config.exhaustion_policy
    
    def can_continue_phase(self, phase: str, remaining_workers: int) -> bool:
        """Check if phase can continue with remaining workers.
        
        Args:
            phase: Phase name
            remaining_workers: Number of workers still to execute
            
        Returns:
            True if phase can continue
        """
        if not self.status:
            return False
        
        # Check exhaustion policy
        if self.is_phase_exhausted(phase):
            policy = self.handle_exhaustion()
            
            if policy == BudgetExhaustionPolicy.FAIL_FAST:
                return False
            elif policy == BudgetExhaustionPolicy.GRACEFUL:
                # Allow current workers to finish
                return False
            elif policy == BudgetExhaustionPolicy.BEST_EFFORT:
                # Continue with reduced budget
                return True
        
        return True
    
    def get_worker_budget_recommendation(self, phase: str, remaining_workers: int) -> int:
        """Get recommended budget per worker.
        
        Distributes remaining budget evenly across remaining workers.
        
        Args:
            phase: Phase name
            remaining_workers: Number of remaining workers
            
        Returns:
            Recommended tokens per worker
        """
        remaining = self.get_phase_remaining(phase)
        
        if remaining_workers <= 0:
            return 0
        
        # Distribute evenly
        recommended = remaining // remaining_workers
        
        # Subtract buffer
        effective = recommended - self.config.worker_buffer
        
        return max(effective, 0)