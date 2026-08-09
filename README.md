# 币安合约跟单系统

实时监控多个币安带单员的仓位变动，按比例在自己的账户自动跟单。

---

## 📖 文档

**[完整使用指南](USER_GUIDE.md)** - 配置、浏览器认证、迁移和部署的主文档

---

## ⚡ 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置（推荐 HTTP + 持久化浏览器认证恢复）
cp config/config.example.yaml config/config.yaml
nano config/config.yaml  # 填写 Bitget 凭证和带单员配置

# 3. 运行本地测试（不启动跟单服务）
python3 -m unittest discover -s tests -v

# 4. 验证配置
python3 -m src.main validate

# 5. 启动系统
python3 -m src.main run
```

---

## 核心功能

- **实时监控** - 每 2-3 秒轮询带单员仓位变化
- **自动跟单** - 检测到变化后自动计算并执行订单
- **比例控制** - 通过 `coefficient` 调节仓位大小
- **风险管理** - 黑名单、冲突检测
- **热重载** - HTTP Cookie 配置可无需重启更新；切换认证来源需重启
- **Telegram 通知** - 实时推送交易和异常信息
- **Bitget UTA 官方接口** - USDT 永续、单向持仓、全仓模式；不依赖 ccxt

---

## 仓位计算公式

```
my_notional = (leader_notional / leader_margin) * my_margin * coefficient
```

**参数说明：**
- `leader_notional`: 带单员仓位名义价值
- `leader_margin`: 带单员总保证金（配置文件中填写）
- `my_margin`: 你的账户余额（系统自动获取）
- `coefficient`: 跟单系数（1.0 = 同比例，2.0 = 加倍，0.5 = 减半）

---

## 模块架构

| 模块 | 职责 |
|------|------|
| `position_source` | 仓位来源契约与 Binance 响应统一解析 |
| `poller` | HTTP Cookie 仓位来源（兼容模式）|
| `binance_auth` | 持久化浏览器认证与浏览器仓位来源 |
| `detector` | 检测仓位变化（开/平/加/减仓）|
| `sizer` | 计算跟单数量 |
| `bitget_executor` | 使用 Bitget UTA 官方 REST API 执行订单 |
| `coordinator` | 协调全流程 |
| `state` | 状态持久化 |
| `notifier` | Telegram 通知 |

---

## 配置示例

```yaml
leaders:
  - name: "Trader_A"
    portfolio_id: "4956682966099962369"
    coefficient: 1.0          # 跟单系数：1=同比例，<1=保守，>1=激进
    total_margin: 50000       # 带单员总保证金
    enabled: true

execution:
  exchange: "bitget"          # 仅支持 Bitget 统一账户（UTA）
  api_key: "YOUR_BITGET_API_KEY"
  api_secret: "YOUR_BITGET_API_SECRET"
  api_passphrase: "YOUR_BITGET_API_PASSPHRASE"
  sandbox: false                # Bitget UTA 不使用此字段

risk:
  blacklist: []               # 黑名单币种
  conflict_resolution: "skip" # 冲突处理策略：skip = 跳过冲突订单
```

完整配置说明见 [USER_GUIDE.md](USER_GUIDE.md)。配置模板见 [config/config.example.yaml](config/config.example.yaml)。

### 浏览器认证数据源

完成 VNC 中的人工 Binance 登录后，推荐启用混合来源：

```yaml
binance_source:
  type: "hybrid"
  browser:
    profile_dir: "data/binance-browser-profile"
    headless: true
  session:
    refresh_interval_hours: 36
```

`hybrid` 模式正常使用快速 HTTP 请求；HTTP 鉴权失败时会使用持久化 browser profile 刷新内存中的请求凭证并立即重试。后台每 36 小时还会执行一次浏览器保活刷新。若 Binance 要求验证码或二次验证，系统会保留安全暂停行为并需要通过 VNC 人工登录。成功返回的空仓位列表会正常触发平仓同步。更改来源类型或浏览器 profile 设置后需要重启服务，不能依赖热重载切换。

---

## 部署

Bitget 使用统一账户（UTA）的 USDT 永续合约、单向持仓和全仓模式。系统启动时会通过官方 API 设置 `one_way_mode`，订单使用单向持仓类型 `net`。订单通过官方 v3 REST API 提交；下单数量以基础币计量并按合约 `quantityMultiplier` 向下取整。系统目标杠杆为 50 倍，合约最高杠杆更低时自动采用该上限。

### systemd 服务

```bash
sudo cp systemd/copy-trader.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now copy-trader

# 查看状态
sudo systemctl status copy-trader

# 查看日志
sudo journalctl -u copy-trader -f
```

### 认证方式

默认部署使用 `binance_source.type: hybrid`。日常仓位读取走快速 HTTP；浏览器会在 HTTP 鉴权失败时刷新进程内的认证数据，并每 36 小时进行一次保活刷新。只有 Binance 要求重新验证、验证码，或 browser profile 损坏时，才需要人工通过 VNC 重新登录。

`binance_source.type: http` 是兼容模式，需要人工维护 Cookie 和相关 header；更新后可以热重载。两种模式的完整操作和迁移流程见 [USER_GUIDE.md](USER_GUIDE.md)。

### Cookie 更新（仅 HTTP 兼容模式）

```bash
# 1. 浏览器重新获取 Cookie
# 2. 更新 config/config.yaml
# 3. 热重载（无需重启）
sudo systemctl reload copy-trader
```

---

## 常用命令

```bash
# 验证配置
python3 -m src.main validate

# 前台运行
python3 -m src.main run

# 服务管理
sudo systemctl start|stop|restart|status copy-trader

# 查看日志
tail -f logs/app.log
```

---

**详细文档**：[USER_GUIDE.md](USER_GUIDE.md)  
**测试**：`python3 -m unittest discover -s tests -v`
