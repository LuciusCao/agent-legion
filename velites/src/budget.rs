//! Built-in run budgets (design §5 预算内建).
//!
//! Three dimensions, all checked BEFORE each model call:
//!
//! - `max_turns` — completed agent turns (assistant completions);
//! - `max_tokens` — cumulative usage (input + output + cacheRead);
//! - wall-clock deadline — derived from `--timeout-seconds`, which bounds
//!   the whole run (individual provider HTTP requests are NOT capped by it;
//!   long generations stream for many minutes, design §7).
//!
//! Exhaustion semantics: the agent loop injects ONE wrap-up message
//! ([`wrap_up_message`]) giving the model a final turn to stop calling tools
//! and write out every declared artifact, then ends the run with
//! `agent_end{reason: "budget_exceeded"}` regardless of how that turn ends.

use std::time::{Duration, Instant};

/// The budgets configured for one run. An absent dimension is unbounded.
pub struct Budget {
    max_turns: Option<u32>,
    max_tokens: Option<u64>,
    deadline: Option<Instant>,
}

/// Which dimension ran out (drives the wrap-up message wording).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BudgetViolation {
    Turns,
    Tokens,
    Deadline,
}

impl Budget {
    pub fn new(max_turns: Option<u32>, max_tokens: Option<u64>, wall_clock: Duration) -> Self {
        Self {
            max_turns,
            max_tokens,
            deadline: Some(Instant::now() + wall_clock),
        }
    }

    /// Whether the budget is exhausted after `turns_completed` turns and
    /// `total_tokens` cumulative tokens. Returns the first violated dimension.
    pub fn exhausted(&self, turns_completed: u32, total_tokens: u64) -> Option<BudgetViolation> {
        if self.max_turns.is_some_and(|max| turns_completed >= max) {
            return Some(BudgetViolation::Turns);
        }
        if self.max_tokens.is_some_and(|max| total_tokens >= max) {
            return Some(BudgetViolation::Tokens);
        }
        if self
            .deadline
            .is_some_and(|deadline| Instant::now() >= deadline)
        {
            return Some(BudgetViolation::Deadline);
        }
        None
    }
}

/// The wrap-up message injected as a user message when the budget runs out:
/// the model gets exactly one final turn to write out declared artifacts.
pub fn wrap_up_message(violation: BudgetViolation) -> String {
    let dimension = match violation {
        BudgetViolation::Turns => "the turn budget (--max-turns)",
        BudgetViolation::Tokens => "the token budget (--max-tokens)",
        BudgetViolation::Deadline => "the wall-clock deadline (--timeout-seconds)",
    };
    format!(
        "SYSTEM NOTICE: {dimension} is exhausted. This is your FINAL turn: \
         do not start new work, keep tool calls to the minimum needed to \
         write out every artifact you declared, then stop."
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unbounded_dimensions_never_violate() {
        // Deadline far in the future; no turn/token limits.
        let budget = Budget::new(None, None, Duration::from_secs(3600));
        assert_eq!(budget.exhausted(1_000_000, u64::MAX - 1), None);
    }

    #[test]
    fn turns_violation() {
        let budget = Budget::new(Some(2), None, Duration::from_secs(3600));
        assert_eq!(budget.exhausted(0, 0), None);
        assert_eq!(budget.exhausted(1, 0), None);
        assert_eq!(budget.exhausted(2, 0), Some(BudgetViolation::Turns));
        assert_eq!(budget.exhausted(3, 0), Some(BudgetViolation::Turns));
    }

    #[test]
    fn tokens_violation() {
        let budget = Budget::new(None, Some(100), Duration::from_secs(3600));
        assert_eq!(budget.exhausted(0, 99), None);
        assert_eq!(budget.exhausted(0, 100), Some(BudgetViolation::Tokens));
    }

    #[test]
    fn deadline_violation() {
        let budget = Budget::new(None, None, Duration::from_millis(0));
        assert_eq!(budget.exhausted(0, 0), Some(BudgetViolation::Deadline));
    }

    #[test]
    fn turns_checked_before_tokens() {
        let budget = Budget::new(Some(1), Some(1), Duration::from_secs(3600));
        assert_eq!(budget.exhausted(1, 1), Some(BudgetViolation::Turns));
    }

    #[test]
    fn wrap_up_message_names_the_dimension() {
        assert!(wrap_up_message(BudgetViolation::Turns).contains("--max-turns"));
        assert!(wrap_up_message(BudgetViolation::Tokens).contains("--max-tokens"));
        assert!(wrap_up_message(BudgetViolation::Deadline).contains("--timeout-seconds"));
    }
}
