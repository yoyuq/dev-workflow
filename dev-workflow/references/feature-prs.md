# Feature PR delivery (GitHub, 2.16)

Use this mode when the user wants the controller to publish, test and merge each feature into one dedicated work branch, then regress the real merged result before advancing. Ordinary local delivery remains `branch_commit_and_report`. No extra skill or Python dependency is required; Git and configured repository credentials are required.

## Interview and plan

Use the bundled grill-me and [requirements alignment](requirements-alignment.md) first. Inspect the current project before proposing improvement directions: distinguish user-visible gaps, existing behavior and technical facts. Ask only unresolved product choices. Give each accepted improvement a concrete result, allowed scope, dependencies and fixed behavioral verification. A frontend/backend split alone is insufficient: each work package should deliver an independently reviewable functional result. Reuse prior answers and actual authorization; never fabricate user confirmation.

For new runs, apply [functional task partitioning](task-partitioning.md): assess complexity, dependencies, uncertainty and recovery risk, record branch boundaries, and configure bounded runtime splitting when automatic repartitioning is wanted. Prefer original checks that can validate individual behaviors while retaining combined acceptance. A runtime split creates functional child PRs, retains the original owner for full acceptance and preserves original budgets; it cannot add product goals or acceptance commands. Existing frozen runs retain their original policy.

Read `origin`, the remote base commit, repository merge settings, CI and branch rules. Pin the authorized remote URL and full baseline SHA. Record known baseline failures and agree their disposition; do not silently remove failing acceptance. Workers receive the functional goal, dependent accepted artifacts and actual failure evidence. They implement code; the Python controller owns GitHub publication and Git transactions.

## Plan fields

Use a coordinated v2 plan with an independent reviewer (including a single-feature run), `delivery: "feature_prs"`, `limits.parallelism: 1`, and the following object. The AI prepares this from verified project facts, not by asking the user to fill JSON:

```json
{
  "provider": "github",
  "repository_url": "https://github.com/OWNER/REPO",
  "base_branch": "main",
  "authorization": {
    "merge_features": true,
    "revert_failed_merge": true,
    "reference": "Actual user message/specification",
    "quote": "Actual permission to merge child PRs and revert failed work"
  },
  "required_checks": [],
  "ci_wait_seconds": 120,
  "rollback_reserve_seconds": 60,
  "post_merge_checks": [
    {
      "id": "parent-regression",
      "argv": ["python", "/absolute/external/pinned_check.py", "{repo}"],
      "timeout_seconds": 30
    }
  ],
  "parent_pr": true
}
```

The object is named `remote_git`. External check files are pinned automatically and must be outside the source checkout. `post_merge_checks` must validate the work branch at each increment and the restored previously accepted state. Feature-specific checks belong to tasks; cumulative task checks run for all accepted features plus the candidate. Add explicit required CI names when needed; discovered branch-required checks are always included. Pending/missing CI blocks merge, failed CI returns diagnostic evidence for repair. Unsupported human/owner approval or merge-queue policies block rather than use admin bypass.

Credentials come from `GH_TOKEN`, `GITHUB_TOKEN` or the normal noninteractive Git credential helper. Plans, reports, prompts and Git arguments contain no token. This cooperative controller is not an OS security boundary; run untrusted workers in appropriately isolated accounts/environments. Do not give a cloud host broad local credentials simply to make a test pass.

## Controller sequence

1. Create `workflow/RUN/integration` on the pinned remote baseline, keeping the original checkout and project base branch intact.
2. Implement one feature in `workflow/RUN/TASK-aN` from the latest accepted work head. Verify and commit only its allowed scope.
3. Publish the child branch and idempotently create its PR targeting this run's work branch. Run fresh fixed feature checks after publication, then independent exact-head AI review.
4. Test a local merge candidate against all accepted features. Obtain any declared human review. Recheck the PR identity, remote work head and GitHub CI; reserve enough original time for post-merge checks, revert and restored checks.
5. Record durable merge intent and merge the child PR. Confirm both parents and the tree match the tested candidate. Fetch the actual remote merge and run cumulative regression plus fixed `post_merge_checks` on it. Only then accept milestones and release the next feature.
6. On failed post-merge regression, including a fixed check that cannot start, persist evidence, ordinary `git revert -m 1` the known merge and push the revert without rewriting history. Confirm the accepted tree is restored and rerun accepted-feature/global checks. Reconciliation permits the local head to be either the recorded previously accepted head or the controller's own recorded merge; an unrelated local head remains blocking. Send the original worker the failure, PR and previous work; the next attempt creates a new child branch/PR under the same budget. Failed restored checks halt further dispatch.
7. After all features, independent whole-branch review and fresh final checks, create an open **draft** parent PR from the work branch to the configured base. No automatic merge of this parent PR.

## Interrupted or blocked runs

Use the same `workflow.py status`, `recover`, `clear-cancel` (when cancellation was recorded) and `run` commands as ordinary coordinated runs. `recover` retains remote intent and clears stale local process/session records after checking them. The subsequent `run` queries PR/ref facts, reconciles completed writes and reruns needed checks before dispatch. Neither step infers acceptance from a local branch alone. Both keep attempts, elapsed time, reviews and pins. Never reset budgets or edit frozen plans to resume.

Unknown write results, authentication/network failure, concurrent parent changes and changed PR heads stop new dispatch and appear in `remote_hold`; fix that external condition and resume the same run. A failed business check uses the ordinary bounded repair/learning path. A remote change with unknown ancestry is retained for investigation, not automatically overwritten or reverted. This first version advances features serially; local coordinated mode still supports parallel work and messages.

`status`, `delivery.json` and the read-only dashboard expose parent/child PRs, exact heads, phases and rollback evidence. No PR link is proof of acceptance: inspect the bound check/review records. Remote writes rely on GitHub permissions, and the host must attach created PRs to its task UI when available.
