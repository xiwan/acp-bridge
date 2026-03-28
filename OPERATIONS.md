# ACP Bridge — Operations Guide

AI agent 开发操作手册。每次启动新 feature 时，按此流程执行。

## Phase 0: Orientation（熟悉代码）

1. 阅读 `README.md` — 架构、Agent Compatibility Matrix、当前版本
2. 阅读 `AGENT_SPEC.md` — ACP 协议规范
3. 阅读 `VERSION` — 当前版本号
4. 浏览核心源码（按依赖顺序）：
   - `main.py` → `src/acp_client.py` → `src/agents.py` → `src/jobs.py`
   - `src/sse.py` → `src/security.py` → `src/store.py` → `src/routes/`
5. 浏览 `config.yaml` 和 `test/` 目录

> **原则：先读后写，不懂不动。**

## Phase 1: Design（设计）

1. 一句话描述 feature 需求
2. 列出需要修改的文件清单
3. 识别是否涉及**稳定代码**（见底部保护清单），如涉及 → **暂停，告知人类确认**
4. 确定版本号（semver: patch/minor/major）

## Phase 2: Test First — Scratch（草稿验证）

在 `test/scratch/` 下写临时验证代码，快速验证 MVP 可行性。

```bash
mkdir -p test/scratch
# 写临时测试脚本
vi test/scratch/try_<feature>.sh
# 运行
bash test/scratch/try_<feature>.sh
```

`test/scratch/` 已在 `.gitignore` 中，**不会被提交**。

此阶段目标：
- 验证核心逻辑可行
- 验证与现有系统的兼容性
- 发现潜在问题，**在写正式代码前暴露风险**

## Phase 3: Implement（实现）

1. 写最少的代码实现 feature
2. **不要**重构无关代码
3. **不要**修改现有测试（除非人类明确要求）
4. 用 scratch 脚本持续验证

## Phase 4: Formal Test（正式测试）

scratch 验证通过后，将测试迁移到正式测试脚本：

1. 创建 `test/test_<feature>.sh`（遵循现有测试风格）
2. 加入 `test/test.sh` 主 runner
3. 运行全量测试，确认无回归：
   ```bash
   ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:18010
   ```
4. 如涉及 Docker 改动，确认构建正常：
   ```bash
   sudo docker compose -f docker/light/docker-compose.yml up -d --build
   ```

## Phase 5: Documentation（文档更新）

1. `README.md` — Matrix、Changelog、Configuration、Project Structure
2. `config.yaml.example` — 新配置项（**无敏感信息**）
3. `skill/SKILL.md` — 版本号、新命令
4. `VERSION` — 版本号

## Phase 6: Pre-commit Checklist（提交前检查）

按顺序执行，全部通过才能提交：

```bash
# 1. 全量测试通过
ACP_TOKEN=<token> bash test/test.sh http://127.0.0.1:18010

# 2. 版本一致性检查
V=$(cat VERSION | tr -d '[:space:]')
grep -q "v${V}" README.md && echo "✅ README" || echo "❌ README"
grep -q "v${V}" skill/SKILL.md && echo "✅ SKILL" || echo "❌ SKILL"

# 3. 敏感信息检查
git diff --cached | grep -iE 'token=.{8}|api.key=.{8}|password=' && echo "❌ 敏感信息!" || echo "✅ 无敏感信息"
```

- [ ] `test/` 下所有测试通过
- [ ] `VERSION` 已更新
- [ ] `README.md` Changelog 版本 = `VERSION`
- [ ] `skill/SKILL.md` 版本 = `VERSION`
- [ ] `config.yaml.example` 无敏感信息
- [ ] `git diff` 无意外改动

## Phase 7: Commit & Push

```bash
git add -A
git commit -m "v<VERSION>: <一句话描述>"
git push
```

---

## 稳定代码保护清单

修改以下文件需要**人类确认**：

| 文件 | 原因 |
|------|------|
| `src/acp_client.py` | 核心 ACP 连接管理，影响所有 agent |
| `src/agents.py` | Agent handler 分发，影响所有调用 |
| `src/security.py` | 认证中间件，影响所有请求 |
| `src/jobs.py` | 异步任务引擎，影响 job 可靠性 |
| `main.py` | 启动流程，影响整个服务 |
| `test/lib.sh` | 测试基础设施，影响所有测试 |
