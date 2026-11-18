# Troubleshooting index

Use this as a short router for known problems. Open the linked canonical record only when its symptom matches. “Evidence” points to the detailed source; proposals are explicitly marked and are not runnable solutions.

## Environment and device

- **Personal Debug link fails on a class/file present in source.** **Confirmed cause:** Git-external Personal Debug `.xcodeproj` was stale after fetch and lacked a Sources membership present in the source project. **Shortest handling:** refresh the Git-external project metadata using the existing preparation procedure, then read back the target membership; the recorded recovery reused the same build and DerivedData. **Stop:** if membership still differs or the next link attempt fails, record the actual diagnostic and stop changing unrelated project/source files. **Evidence:** [BUG20261026700694 context, 2026-10-26 15:11–15:13](/Users/mr.yao/.codex/work-agent/tools/mcloud/dashboard/records/bug/BUG20261026700694/context.md).
- **Device unavailable, Mac locked, or authentication required.** **Handling:** capture the specific blocker once, release the shared resource when possible, and continue independent authorized work; resume only after the condition changes. **Stop:** do not rotate agents through the same unchanged blocker. **Evidence path:** `references/execution-notes.md` (existing stop/resume rule).

- **增量构建前不必机械刷新个人工程。** 个人定制pbxproj的原始hash本就可能与业务仓不同，不能据此判过期；先核相关fileRef/Sources成员与镜像源symlink或内容。工程/依赖未变且源同一时直接复用现有工程和DerivedData；只有实际映射过期才prepare。本轮仅改一个.m、跳过prepare后成功增量构建10271430，证据见[701847卡](/Users/mr.yao/Documents/Project/mcloud_ios_client_retry/docs/bugs/cards/2026-10-27_BUG20261027701847.md)。

## Device and interface

- **READY仍读已退出的PDUIApp / kAXErrorServerNotFound。** 先按插件规则核实服务及有界重读；恢复job到serving即可验READY，不等长驻Runner终态。若当前任务明确授权启动已安装目标App，可用公开App激活作为恢复步骤，再取完整READY；不把激活/服务可探测当通过，不重签重建或循环启动，不绕过锁屏认证。真实错误回归、已验证操作及未测边界见[本次工具修复记录](/Users/mr.yao/.codex/work-agent/tools/mcloud/dashboard/reports/pua-foreground-recovery-20261027.md)。
- **Attachment review enters edit mode or old AX target points elsewhere.** **Confirmed cause:** stale AX indices/coordinates were reused; iframe scrolling focused the rich-text editor, and image-node clicks only focused the node. **Shortest handling:** refresh AX and screenshot before acting; do not scroll the iframe AX node; if edit mode opens, use the fresh visible Cancel control and read back that the page is read-only. **Stop:** do not save; if the fresh screen does not show a reliable Cancel target, stop UI actions. **Evidence:** [LESSONS.md, BUG20261021698030 tool/automation lesson](/Users/mr.yao/.codex/work-agent/tools/mcloud/bugfix/LESSONS.md).
- **A view has a shadow property but the shadow is not visible.** **Confirmed cause:** an opaque foreground collection view covered the footer shadow. **Shortest handling:** inspect actual view layering and current-package screenshots on each affected page. **Stop:** do not accept based only on a layer property or static source inspection. **Evidence:** [LESSONS.md, BUG20261021698030 visual acceptance lesson](/Users/mr.yao/.codex/work-agent/tools/mcloud/bugfix/LESSONS.md).

## Platform writes and scheduling/handoff

- **Write target, record ID, or revision differs from the intended item.** **Handling:** stop writes for that target, reread the exact record, and report the mismatch; never guess a repair or rollback. This preserves the existing compare-and-set boundary; no automatic repair/rollback is available.
- **A task appears to need redispatch or may have been dispatched twice.** **Current handling:** inspect the exact existing record and its latest revision/status before dispatch; continue/update that record when it already represents the work. **Status:** an atomic phase/report consistency helper intended to reduce duplicate dispatch is only a proposal; it is not implemented. Do not claim it ran. **Evidence:** [BUG WORKFLOW, unique BUG record and CAS guidance](/Users/mr.yao/.codex/work-agent/tools/mcloud/bugfix/WORKFLOW.md); [2026-10-27 workflow cost review, “状态收尾” and “协调模型”](/Users/mr.yao/.codex/work-agent/tools/mcloud/bugfix/reviews/workflow-cost-review-20261027.md).
- **A read-only child reports completion while authorized end-to-end work remains.** **Confirmed process failure:** the coordinator ended at “analysis complete” and constrained the worker to read-only, so repair, current-package verification, review, and authorized delivery did not proceed. **Shortest handling:** coordinator resumes the existing authorized phases and names the next owner/action. **Stop:** wait only for a real ambiguity or blocker that prevents the next authorized action; a handoff/report alone is not closure. **Evidence:** [LESSONS.md, BUG20261021698030 supervision lesson](/Users/mr.yao/.codex/work-agent/tools/mcloud/bugfix/LESSONS.md).

## Code and business behavior

- **Music search selection, empty state, navigation status bar, short-list scrolling, cross-source candidate/audition ownership, source restoration, page-view exposure, or a personal Debug fixture not opening.** Check only the matching titled lesson in [LESSONS.md](/Users/mr.yao/.codex/work-agent/tools/mcloud/bugfix/LESSONS.md); each entry gives its trigger, evidence-backed cause, prevention, and tested scope. Do not generalize beyond that scope.
- **Cause is unresolved or prior evidence conflicts with the current source/package.** Keep the conclusion tentative, record what is missing, and follow the current BUG confidence and acceptance route. **Evidence:** [references/bug-confidence.md](bug-confidence.md).

## Proposed tools, not available

The cost review also proposed build preflight/receipts, known device navigation recipes, safe platform-write adapters, and state-consistency tooling. These were not implemented or validated. Use only the existing documented procedures; do not present a proposal as a known fix. **Evidence:** [workflow cost review, “优先固化” and “协调模型与实施顺序”](/Users/mr.yao/.codex/work-agent/tools/mcloud/bugfix/reviews/workflow-cost-review-20261027.md).
