# 场景测试 (Scene Tests)

基于 `skill/SKILL.md` 中定义的技能工作流，覆盖真实用户场景。

## 测试列表

| 文件 | 场景 | 需要 Bridge | 预计耗时 |
|------|------|-------------|----------|
| `scene_single_call.sh` | 单次调用：快速问答、指定 agent、card 输出 | ✅ | ~60s |
| `scene_multi_turn.sh` | 多轮对话：/chat 进入、上下文保持、退出 | ✅ | ~60s |
| `scene_pipeline.sh` | 多 agent 编排：relay、dual-view、review-write-test | ✅ | ~5min |
| `scene_async_job.sh` | 异步任务：提交、查询、完成 | ✅ | ~30s |
| `scene_dynamic_harness.sh` | 动态 harness：创建专用 agent、调用、清理 | ✅ | ~60s |

## 运行

```bash
# 全部场景
ACP_TOKEN=<token> bash test/scenes/run_all.sh

# 单个场景
ACP_TOKEN=<token> bash test/scenes/scene_single_call.sh
```
