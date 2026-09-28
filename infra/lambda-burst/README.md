# Lambda Burst — Serverless Agent Scaling

将 harness-factory agent 部署为 AWS Lambda，实现突增 100+ 并发 agent 实例。
对调用方完全透明。

> ⚠️ **状态：已实现，暂不建议生产使用。**
>
> Bridge 侧代码完整、单测充分（v0.45.0 发布前的自审计修掉了 5 个缺陷），但
> **从未对真实 Lambda 做过端到端验证**——所有测试都是 mock/moto。功能默认
> 关闭，参考 `config.yaml` 里没有 `lambda_pool` 段。
>
> 部署前置（default VPC **不满足**）：Lambda 需 **私有子网 + NAT 出网**
> （全公有子网的 VPC 会被 CDK 拒绝——公有子网里的 Lambda 无法访问互联网）；
> 目标 region 需已 CDK bootstrap；部署身份需要
> CloudFormation/Lambda/IAM/EC2/SecretsManager 权限。
>
> 当作实验性后端对待：在合适的 VPC 里部署验证没问题，但在跑通至少一次真实
> 调用之前，不要用于生产 fan-out。

## 架构

```
 VPC (用户已有)
 ┌──────────────────────────────────────────────────────────┐
 │  Private Subnet                                          │
 │                                                          │
 │  ┌──────────────┐         ┌─────────────────────────┐   │
 │  │    EC2       │ invoke  │    Lambda × N (N≤100)   │   │
 │  │ ┌──────────┐ │────────▶│ ┌─────────────────────┐ │   │
 │  │ │ACP Bridge│ │         │ │  handler.py         │ │   │
 │  │ │  :18010  │ │◀────────│ │  (ACP JSON-RPC)     │ │   │
 │  │ └──────────┘ │ result  │ ├─────────────────────┤ │   │
 │  │ ┌──────────┐ │         │ │  Layer: /opt/bin/   │ │   │
 │  │ │ LiteLLM  │ │◀─ ─ ─ ─│ │  harness-factory    │ │   │
 │  │ │  :4000   │ │  http   │ └─────────────────────┘ │   │
 │  │ └──────────┘ │         └─────────────────────────┘   │
 │  └──────────────┘                                        │
 │                                                          │
 │  全程 VPC 私网，LiteLLM 不暴露公网                         │
 │  API Key 存 Secrets Manager，运行时读取                    │
 └──────────────────────────────────────────────────────────┘
```

## Pool 层次

```
┌───────────────────────────────────────────┐
│          /runs  /pipelines  /jobs          │
├───────────────────────────────────────────┤
│               Pool Router                  │
│   route(agent) → local | remote | lambda   │
├───────────┬─────────────┬─────────────────┤
│ LocalPool │ RemotePool  │   LambdaPool    │
│ (existing)│ (mesh peer) │   (dynamic)     │
│ subprocess│  HTTP/A2A   │ lambda:Invoke   │
│  stateful │  stateful   │  stateless      │
│ max ~12   │ peer limits │  max 100+       │
└───────────┴─────────────┴─────────────────┘
```

## 前置条件

| # | 条件 | 说明 |
|---|------|------|
| 1 | AWS 账户 | 需要 Lambda, IAM, VPC, Secrets Manager 权限 |
| 2 | CDK v2 | `npm install -g aws-cdk` |
| 3 | VPC | Lambda 和 EC2(LiteLLM) 在同一 VPC 的 private subnet |
| 4 | Security Group | Lambda SG 允许出站到 LiteLLM 端口 + HTTPS(443) |
| 5 | harness-factory 源码 | 需 `CGO_ENABLED=0` 编译为 static binary |
| 6 | Secrets Manager | API key 存这里，不做明文环境变量 |

## 安全设计

| 关注点 | 处理方式 |
|--------|---------|
| API Key | Secrets Manager 存储，Lambda 运行时读取并缓存（warm container） |
| 网络 | Lambda 在 VPC private subnet，无公网出口（除 443 for Bedrock） |
| SG 最小权限 | 只开 LiteLLM 端口 + HTTPS，不允许全出站 |
| IAM | Lambda role 仅有 SecretsManager:GetSecretValue + Bedrock:InvokeModel |
| /tmp 隔离 | 每次调用独立 session_id 目录，执行后清理 |
| 无密钥明文 | CDK template、环境变量、日志均不含密钥值 |

## 部署步骤

### 1. 编译 harness-factory

```bash
cd /path/to/ACP-Harness-Factory
CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" \
  -o harness-factory-lambda ./cmd/harness-factory

# 放入 Layer 目录
mkdir -p /path/to/acp-bridge/infra/lambda-burst/layer/bin
cp harness-factory-lambda infra/lambda-burst/layer/bin/harness-factory
chmod +x infra/lambda-burst/layer/bin/harness-factory
```

### 2. 配置 CDK 环境

```bash
cd infra/lambda-burst/cdk
cp .env.example .env
# 编辑 .env：填入 VPC_ID, SUBNET_IDS, LITELLM_URL
```

### 3. 部署

```bash
npm install
cdk bootstrap   # 首次
cdk deploy
```

### 4. 填入 API Key

CDK 部署后输出 `SecretArn`，填入真实 API key：

```bash
aws secretsmanager put-secret-value \
  --secret-id /acp-bridge/lambda-burst/litellm-api-key \
  --secret-string "sk-your-actual-litellm-key"
```

### 5. 配置 Bridge

在 `config.yaml` 中添加：

```yaml
lambda_pool:
  enabled: true
  function_name: "acp-bridge-harness-burst"  # CDK 输出的 FunctionName
  region: "us-east-1"
  max_concurrent: 100
  timeout: 300
  default_model: "bedrock/anthropic.claude-sonnet-4-6"
```

### 6. 重启 Bridge

```bash
./bridge-ctl.sh restart
```

## API 使用

### 预热（拉起 N 个 Lambda 容器）

```bash
curl -X POST http://localhost:18010/lambda-pool/scale \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -d '{"count": 100}'
```

### 查看状态

```bash
curl http://localhost:18010/lambda-pool/status \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"

# Response:
# {"function_name": "acp-bridge-harness-burst", "active": 3, "max_concurrent": 100, ...}
```

### 单次调用

```bash
curl -X POST http://localhost:18010/lambda-pool/invoke \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -d '{
    "prompt": "Write a Python function to parse CSV files",
    "profile": {"tools": {"fs": {"permissions": ["read", "write"]}}},
    "model": "bedrock/anthropic.claude-sonnet-4-6"
  }'
```

### 批量并行调用（突增 100 个任务）

```bash
curl -X POST http://localhost:18010/lambda-pool/invoke-batch \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN" \
  -d '{
    "prompts": [
      {"prompt": "Review file1.py for bugs"},
      {"prompt": "Review file2.py for bugs"},
      ...
    ],
    "profile": {"tools": {"fs": {"permissions": ["read"]}}},
    "model": "bedrock/deepseek.v3.2"
  }'
```

### 缩容

```bash
curl -X POST http://localhost:18010/lambda-pool/drain \
  -H "Authorization: Bearer $ACP_BRIDGE_TOKEN"
```

## 成本

| 维度 | 值 |
|------|-----|
| Lambda 512MB × 60s | ~$0.0005/次 |
| 100 并发 burst × 60s | ~$0.05 |
| Layer 存储 (10MB) | $0.01/月 |
| Secrets Manager | $0.40/月 + $0.05/10K次API调用 |
| VPC ENI | 无额外费用 |

> 注：LLM token 费用远大于 Lambda 计算费用（通常 100x+）。

## 限制与规避

| 限制 | 值 | 规避方案 |
|------|-----|---------|
| 无状态 | 每次调用独立 | prompt 中传入足够上下文 |
| Lambda 超时 | 最长 15 分钟 | 复杂任务拆分为 pipeline 多步 |
| Response 大小 | 同步 6MB | 大结果写 S3 返回 presigned URL |
| 并发 quota | 默认 1000/region | AWS 申请提升至 10000+ |
| 冷启动 | ~800ms (Go binary) | 预热 `POST /lambda-pool/scale` |
| /tmp 存储 | 最大 10GB | CDK 配了 1GB，需要更多可调整 |

## 文件结构

```
infra/lambda-burst/
├── README.md              ← 本文件
├── cdk/
│   ├── app.ts             ← CDK 入口
│   ├── stack.ts           ← Stack 定义（Lambda + Layer + VPC + IAM + Secrets）
│   ├── cdk.json
│   ├── package.json
│   ├── tsconfig.json
│   └── .env.example       ← 配置模板
├── wrapper/
│   └── handler.py         ← Lambda handler（ACP JSON-RPC wrapper）
└── layer/
    ├── build.sh           ← harness-factory 编译脚本
    ├── bin/               ← (gitignored) 编译产物
    └── README.md
```
