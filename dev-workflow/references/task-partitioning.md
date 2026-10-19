# Functional branch boundaries (2.16)

For new feature-PR runs, investigate the repository and grill-me decisions before choosing branches. Partition by independently observable outcomes, dependencies and recovery boundaries. A diagram's internal steps are not automatically separate PRs. No extra user questionnaire or new skill installation is needed.

For each original task, record `partition`: `outcomes` (concrete behavior strings), `complexity` and `risk` (low/medium/high), `risk_reasons`, `estimate_seconds`, `boundary` (independent/atomic), `decision` (keep/split/investigate), `rationale` and `evidence` (actual source paths, contracts or observations). Describe hard interactions, uncertain interfaces, data loss/privacy/compatibility risk and prerequisites; do not assign a high score merely because a frontend and backend are both involved. A schema cannot establish whether these business judgments are true.

Prefer a separate branch when one output has its own behavior check and can be reviewed, accepted and reverted without corrupting another output. Sequence shared contracts or compatible preparation before consumers; preserve prerequisite edges. Keep an inseparable migration and its compatibility guard atomic, with a concrete reason and stronger original verification. If an uncertain prerequisite prevents a credible boundary, investigate within scope first. File/line size is supporting evidence, not a reason to break an invariant or manufacture more features.

Optional coordinated-plan policy (AI fills these fields):

```json
{
  "partitioning": {
    "thresholds": {
      "max_outcomes": 3,
      "max_dependencies": 3,
      "max_estimated_seconds": 1800,
      "max_changed_files": 8,
      "max_changed_lines": 400,
      "max_attempt_seconds": 900
    }
  },
  "replanning": {
    "max_revisions": 3,
    "max_added_tasks": 8,
    "max_total_attempts": 16,
    "preparation_checks": [],
    "allow_feature_split": true
  }
}
```

These are configurable prompt thresholds, not universal size limits. Run `python WORKFLOW_ROOT/scripts/workflow_partition.py audit --plan PLAN` while preparing. High complexity/risk, dependency fan-in, multiple outcomes or long estimates flag a decision. Rework a `split` draft into functional tasks before initializing; a justified `keep` retains all checks and review. New runs enabling runtime sizing require a configured AI coordinator. Preserve old frozen runs and opt-in policy; do not rewrite their plans/pins to upgrade them.

During execution, workers can report scoped complexity or dependency facts through blocked/change_request. At a successful implementation handoff, the controller also inspects actual changed leaves (including untracked deliverables), added/deleted text lines and elapsed attempt time. Crossing a runtime threshold retains the draft and blocks publication until the coordinator decides. Binary files count as files, not invented text lines. This is checkpoint-based sizing, not continuous interruption of a running worker, and estimates are not a guarantee of runtime/cost.

The coordinator may use the [functional split protocol](replanning.md) to add at least two children whose checks select existing verification IDs from their original owner. Children inherit scope bounds, dependencies, contracts and resource locks; each obtains its own tests, independent review and feature PR. The original owner remains, retains **all** original acceptance, and releases its milestones only after every child is accepted and the full result passes. It may implement remaining gaps. If no source change remains, the controller verifies the actual work head and performs full parent review/regression without publishing an empty PR. Child review starts at its launch base; parent review starts before the split so it includes the combined work.

An atomic exception uses `partition_keep` with the current `partition_request.draft_files` hashes and an evidence-based rationale. It resumes that exact draft and attempt, preserves checks/review and is logged. It cannot approve work, extend budgets or clear a retry/human gate. Functional splits preserve consumed parent attempts and original time/review/total-attempt budgets; failed original full checks never become success because local child checks passed. If checks cannot validate a proposed partial behavior, keep the work atomic or prepare a new authorized plan rather than invent weaker checks at runtime.

Before activating a split, reserve the minimum remaining attempts for every unfinished owner, ancestor, existing sibling and new child, including each original owner's full acceptance. Already-running work and quota resumptions reuse their current attempt. A proposal that cannot finish within the original total/per-task attempt limits is rejected before its revision becomes active.

If parent/final review finds a concrete defect owned by a child, normal review feedback reopens that owner and invalidates dependent results. The waiting parent yields its serial slot to unfinished descendants; it cannot release an unrelated feature first. An accepted functional dependent whose code remains unchanged may be reverified on the new actual work head, with fresh fixed checks and exact-head independent review, instead of creating a cosmetic commit/empty PR. Original attempts are still consumed, and full parent acceptance remains mandatory.

Inspect `partition_requested`, `partition_kept`, `plan_revised`, child PRs, `split_parent_regression_passed` and requirement evidence in the same run's status/events/report/dashboard. Split explanations and drafts remain evidence, not model self-certification.
