# S01 system controls

The Skill contains service intent, evidence lookups, wording, and examples. Engineering owns:

- The existing daily 07:00 task and frozen-environment clock, expiry, retries and task receipts (`agent_plan`).
- Current business date, exact schedule/lesson matching and visible completion are rechecked inside `deliver_message`. There is no independent unlock precondition or model-facing `check_s01`. Internal 07:00 scheduling remains. Historical unlock fields are operator-only.
- Contact pause, passive-chat priority, contact interval, durable outbox and idempotency (`s01_delivery`, `wechat_test`). An uncertain send is not retried or reported as complete.
- Preparation: require current-task schedule and learning lookups, then an exact lesson knowledge attempt before sending. No lesson/completed paths end without reading course content. A failed knowledge lookup may lead to a simple invitation; no lookup is a repairable omission, not evidence that content is unavailable. The sender preserves the draft or returns a reason; it never silently rewrites it. Shared message composition and constraints live in `reply-style`.
- Receipts: platform acceptance places S01 in `awaiting_delivery`; only confirmed `sent` marks it done. Receipt reconciliation does not re-run the model or resend.
- Model boundary (`agent_view`): environment labels and fixture provenance stay in operator records; model requests use business data and business time. Teacher simulations keep raw mock receipts for audit but present the same business-shaped interface to the model. Sandbox sending never uses the real connector.

The operator workbench retains environment identity and raw audit evidence. No business clock, session, recipient binding or old delivery record is reset during release. S01 version history is published through Skill Studio.
