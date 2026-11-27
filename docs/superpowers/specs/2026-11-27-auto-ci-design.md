# Auto 更新后的完整 CI 检查

## 目标

Auto 更新并推送 master 后，对该次更新的确切提交执行现有完整 CI；只有 CI 全部成功，才创建发布标签并调用 Release。

## 方案

推荐给现有 ci.yml 增加 workflow_call 和可选的 ref 输入，由 Auto 显式调用。保留原有 master push 和 pull_request 触发方式。未传 ref 时沿用触发事件的提交，保持 PR 合并结果的检查行为。

另一个方案是通过 workflow_dispatch 启动独立 CI，再轮询结果。它需要额外的触发权限和等待逻辑，本次选择直接复用，CI 结果显示在同一次 Auto 运行内。

## 执行顺序

1. update：保留现有更新、单元测试及配置校验，创建提交并推送 master，输出提交 SHA 和目标版本；此时不创建标签。
2. ci：有更新且非 dry_run 时，调用 ci.yml，传入 update 输出的 SHA。Linux Python 3.10–3.13 和 Windows PowerShell 检查均检出这一 SHA。
3. tag：等待完整 CI 成功，检出同一 SHA，沿用现有北京时间日期和序号规则创建、推送标签。
4. release：使用 tag 输出的标签调用现有 release.yml。

## 边界和失败行为

- 无更新：不调用完整 CI，不打标签，不发布。
- dry_run：保留当前行为，在 runner 更新文件并运行现有本地校验，不推送提交，不调用新增的完整 CI，不打标签，不发布。
- CI 失败：Auto 失败，停止打标签和发布；已经推送的 master 提交保留。该顺序遵循更新 master 后再检查的要求。
- 后续有人更新 master：CI 和标签仍绑定本次输出的 SHA，避免检查和发布不同提交。
- 人工推送 master 或创建、更新 PR：原有独立 CI 行为保持。
- 人工标签发布、手动 Release、现有 GITHUB_TOKEN 和机器人身份保持。
- 本次不扩展自动补发或同名 Release 的恢复能力。

## 修改及验证范围

修改 .github/workflows/ci.yml、.github/workflows/auto.yml 和 README.md。release.yml 继续复用。

验证三个工作流的 actionlint 检查、依赖和条件分支、精确 SHA 传递、现有 Python 单元测试及配置锁文件校验。真实的 Windows 和 GitHub 调度行为需在提交推送后由 Actions 验证，本次不触发真实发布。
