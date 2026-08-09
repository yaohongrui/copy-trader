# 币安合约跟单系统 - 完整使用指南

实时监控币安带单员的仓位变动，按比例在自己的账户自动跟单。

---

## 目录

1. [前置准备](#前置准备)
2. [配置文件填写](#配置文件填写)
3. [认证方式与 Cookie/header 行为](#认证方式与-cookieheader-行为)
4. [本地测试](#本地测试)
5. [验证配置](#验证配置)
6. [启动系统](#启动系统)
7. [迁移到新服务器](#迁移到新服务器)
8. [Cookie 管理](#cookie-管理)
9. [故障恢复](#故障恢复)
10. [常见问题](#常见问题)

---

## 前置准备

### 1. 环境要求

```bash
# Python 3.10+
python3 --version

# 安装依赖
pip install -r requirements.txt
```

### 2. 需要准备的信息

- [ ] **币安认证方式** - 浏览器 profile（推荐）或 Cookie（兼容模式）
- [ ] **带单员 Portfolio ID** - 带单员详情页 URL 中的数字
- [ ] **带单员总保证金** - 带单员的合约账户总资金（USDT）
- [ ] **Bitget UTA API 凭证** - API Key、Secret 和 Passphrase（需开通 UTA 读写权限）

## 认证方式与 Cookie/header 行为

项目支持两种 Binance 仓位来源：

- `browser`：推荐。Playwright 使用持久化 Chromium profile，页面自动携带当前 Cookie、CSRF、WAF 和设备相关 header。
- `http`：兼容模式。程序直接使用 `binance_web` 中的 Cookie/header，需要人工维护。

### HTTP 兼容模式

⚠️ **重要**：Cookie 包含你的登录凭证，请妥善保管！

**步骤：**

1. 登录币安网站：https://www.binance.com
2. 打开带单员详情页：`https://www.binance.com/zh-CN/copy-trading/lead-details/{portfolioId}`
3. 按 F12 打开开发者工具
4. 切换到 **Network（网络）** 标签
5. 刷新页面
6. 找到 `positions` 请求，点击查看
7. 在 **Headers** 中找到 `Cookie`，复制完整内容

**Cookie 格式示例：**
```
p20t=xxx; BNC-LOCATION=CN; BNC_FV_KEY_EXPIRE=xxx; ...
```

**必需字段：**
- `p20t` - Session 令牌（最关键）
- `csrftoken` - CSRF 验证
- `BNC-LOCATION` - 地区
- `bnc-uuid` - 设备 UUID

**有效期：** Session 令牌有效期约 7-30 天，过期后需要重新获取。

### HTTP header 同步与响应错误处理

HTTP 模式除了 Cookie，还可能依赖 `csrftoken`、`bnc-uuid`、`device-info`、`fvideo-id`、`fvideo-token`、`BNC-Level` 和 User-Agent。只更新 Cookie 不一定足够；如果 Binance 返回鉴权错误、WAF 错误或响应格式错误，应重新从浏览器 Network 中复制完整请求数据。成功返回空数组表示带单员当前没有仓位。

项目提供只更新配置字段的脚本：

```bash
./update_headers.sh --dry-run
./update_headers.sh
sudo systemctl reload copy-trader
```

`--dry-run` 不写配置；正式更新会生成 `config/config.yaml.bak`。脚本不会自动重启或 reload 服务。成功返回的空数组表示带单员当前没有仓位，程序会正常生成平仓信号；网络错误、鉴权错误或响应格式错误会被识别为轮询错误，连续三次后暂停并告警，避免把错误响应当成清仓。

### 浏览器首次登录

服务器通过 VNC 打开的 Chromium 由人工完成验证码、风控验证和二次验证；程序不尝试绕过这些验证。登录成功后，将配置切换为：

```yaml
binance_source:
  type: "hybrid"
  browser:
    profile_dir: "data/binance-browser-profile"
    headless: true
    timeout_ms: 30000
  session:
    refresh_interval_hours: 36
```

该目录等同登录凭证：不要提交、备份到公开位置或授予其他用户读取权限。需要重新人工登录时，先停止跟单服务，再执行 `./binance_browser_login.sh`。若浏览器认证失效，完成登录后重启服务。

### 是否需要更新 Cookie/header？

在 `hybrid` 模式下，通常不需要手工更新 Cookie 或 header：

1. 浏览器登录态保存在 `data/binance-browser-profile`。
2. HTTP 鉴权失败时，浏览器自动使用当前 profile 访问 Binance，并将当前认证数据更新到进程内 HTTP 请求。
3. 后台每 36 小时执行一次相同的浏览器保活刷新。
4. 程序不会把 Cookie/token 打印到日志或写回配置文件。

这不是永久免维护。以下情况仍可能需要人工通过 VNC 登录：Binance 让会话重新验证、WAF/验证码触发、账号主动退出、profile 损坏、浏览器或系统环境变化。程序会把认证失败识别为错误并暂停跟单，不会把认证失败误判成“全部清仓”。恢复后需要重启跟单服务；运行中的服务会独占 profile。

`hybrid` 模式启动时使用 `binance_web` 的 HTTP 配置；运行中浏览器刷新会覆盖进程内认证数据。修改 `binance_source.type` 或 `binance_source.browser` 参数需要重启服务；HTTP Cookie/header 和混合模式的保活间隔支持热重载。

---

## 配置文件填写

### 1. 复制配置模板

```bash
cp config/config.example.yaml config/config.yaml
```

### 2. 填写必需项

编辑 `config/config.yaml`：

```yaml
# ========== 币安 Web 配置（抓取带单员数据） ==========
binance_web:
  # 完整 Cookie 字符串（从浏览器复制）
  cookie: '你的Cookie字符串'
  
  # 从 Cookie 中提取以下字段（可选，留空则从 cookie 自动解析）
  csrf_token: ''
  bnc_uuid: ''
  fvideo_id: ''
  device_info: ''

# ========== 带单员配置 ==========
leaders:
  - name: "Trader_4956682966"              # 自定义名称（方便识别）
    portfolio_id: "4956682966099962369"    # 带单员的 Portfolio ID
    coefficient: 1.0                        # 跟单系数（1.0 = 同比例，2.0 = 加倍，0.5 = 减半）
    total_margin: 39170                     # 带单员总保证金（USDT）
    enabled: true                           # 是否启用

# ========== 交易执行配置 ==========
execution:
  exchange: "bitget"                       # 仅支持 Bitget 统一账户（UTA）
  api_key: "YOUR_BITGET_API_KEY"
  api_secret: "YOUR_BITGET_API_SECRET"
  api_passphrase: "YOUR_BITGET_API_PASSPHRASE"
  sandbox: false                            # Bitget UTA 不使用此字段

# ========== Telegram 通知（可选） ==========
notifications:
  telegram:
    enabled: false                         # 是否启用
    bot_token: ""                          # Bot Token（从 @BotFather 获取）
    chat_id: ""                            # Chat ID（从 @userinfobot 获取）
```

### 3. 仓位计算公式

```
my_notional = (leader_notional / leader_margin) * my_margin * coefficient
```

**参数说明：**
- `leader_notional`: 带单员该仓位的名义价值（USDT）
- `leader_margin`: 带单员的总保证金（配置中的 `total_margin`）
- `my_margin`: 你的账户总保证金（系统自动获取）
- `coefficient`: 跟单系数（配置中的 `coefficient`）

系统将带单员的**当前总仓位名义价值**换算为目标总仓位；发生加仓时，只下目标总仓位与当前实际仓位的差额。低价币的币数量可能很大，应始终以 `target_notional`（USDT）判断仓位大小。

**系数示例：**
- `coefficient = 1.0`：与带单员相同风险比例
- `coefficient = 2.0`：加倍仓位（更激进）
- `coefficient = 0.5`：减半仓位（更保守）

### 4. 风险控制配置（可选）

```yaml
risk:
  blacklist:                      # 黑名单（不跟单的币种）
    - "PEPEUSDT"
    - "SHIBUSDT"
  conflict_resolution: "skip"     # 多带单员冲突策略：skip = 跳过
```

---

## 本地测试

项目当前使用标准库 `unittest`，测试不会启动跟单服务、浏览器或下单流程：

```bash
python3 -m unittest discover -s tests -v
```

浏览器认证的真实检查使用只读命令，必须在完成人工登录后执行：

```bash
DISPLAY=:99 python3 -m src.binance_auth check \
  --profile data/binance-browser-profile \
  4956682966099962369
```

该命令会访问 Binance 页面，因此不属于离线单元测试；迁移或重新登录时按需执行。

---

## 验证配置

运行验证命令，检查配置是否正确：

```bash
python3 -m src.main validate --config config/config.yaml
```

浏览器来源的常规校验和跟单服务应配置 `headless: true`，因此不需要设置
`DISPLAY`。只有运行 `binance_browser_login.sh` 进行人工登录或验证时，才使用
VNC 虚拟桌面。

推荐使用 `binance_source.type: hybrid`：日常仓位读取仍走 HTTP，认证失败时用
持久化浏览器 profile 自动刷新内存中的 Cookie/header 并重试；后台默认每 36 小时
刷新一次。Binance 要求验证码或二次验证时，仍需通过 VNC 人工完成登录。

**预期输出：**
```
Config loaded: 1 leader(s) configured
  [Trader_4956682966] OK - 3 active position(s)
  [Account] OK - balance: 10000.00 USDT
Validation complete.
```

**如果失败：**
- `FAILED - auth error`：浏览器模式检查 profile 登录态；HTTP 模式检查 Cookie
- `FAILED - connection error`：网络问题或 API Key 错误

---

## 启动系统

### 1. 前台运行（测试用）

```bash
python3 -m src.main run --config config/config.yaml
```

观察日志输出，检查是否正常工作。按 `Ctrl+C` 停止。

### 2. 后台运行（生产环境）

使用 systemd 服务：

```bash
# 复制服务文件
sudo cp systemd/copy-trader.service /etc/systemd/system/

# 编辑服务文件（修改路径和用户）
sudo nano /etc/systemd/system/copy-trader.service

# 启用并启动服务
sudo systemctl daemon-reload
sudo systemctl enable copy-trader
sudo systemctl start copy-trader

# 查看状态
sudo systemctl status copy-trader

# 查看日志
sudo journalctl -u copy-trader -f
```

### 3. 系统日志

日志文件位置：`logs/app.log`

```bash
# 实时查看日志
tail -f logs/app.log

# 搜索错误
grep ERROR logs/app.log
```

### 4. 整点余额数据库

每次整点 health check 成功获取 Bitget 账户余额后，会追加到 SQLite 数据库：
`data/health.db`。数据库表为 `hourly_balance`，字段是 `checked_at` 和
`balance_usdt`，可用于后续计算收益率和最大回撤。数据库路径可在配置中修改：

```yaml
health_database:
  path: "data/health.db"
```

例如查看最近记录：

```bash
sqlite3 data/health.db \
  'select checked_at, balance_usdt from hourly_balance order by checked_at desc limit 20;'
```

---

## 迁移到新服务器

下面流程按“旧服务器仍在运行、新服务器准备完成后再切换”的方式编写。新旧服务不能同时使用同一个 Bitget 账户跟单，否则可能重复下单。

### 1. 迁移前确认

确认旧服务使用的项目目录、配置路径和 systemd 服务名：

```bash
sudo systemctl status copy-trader
sudo systemctl status binance-browser-desktop
sudo systemctl cat copy-trader
```

重点记录：

- `WorkingDirectory` 和 `ExecStart` 中的项目路径
- `config/config.yaml`
- `data/health.db`
- `~/.copy-trader/state.json`（跟单状态，必须谨慎迁移）
- `data/binance-browser-profile`（浏览器登录凭证，建议不要跨服务器复制）

如果配置或日志中曾经暴露过 Bitget API 密钥、Telegram token 或浏览器 Cookie，迁移前先在对应平台轮换这些凭证。不要把真实 `config.yaml`、Cookie 或 profile 提交 Git。

### 2. 旧服务器备份并停止服务

先让系统自然保存状态，再停止跟单服务。不要在新旧服务器同时启动跟单服务：

```bash
sudo systemctl stop copy-trader
sudo tar --exclude='data/binance-browser-profile' \
  -czf /tmp/copy-trader-migration.tar.gz -C /root copy
sudo cp ~/.copy-trader/state.json /tmp/copy-trader-state.json
```

如果旧项目不在 `/root/copy`，把命令中的路径替换为实际 `WorkingDirectory`。备份完成后可暂时恢复旧服务，直到新服务器准备好，但切换时必须确保只有一台服务运行。

### 3. 新服务器安装系统依赖

以 Debian/Ubuntu 为例：

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv xvfb fluxbox x11vnc openssh-server
sudo mkdir -p /root/copy
sudo tar -xzf /tmp/copy-trader-migration.tar.gz -C /root
cd /root/copy
```

将项目复制到新服务器的方式可以是 `scp`、内网文件传输或受保护的备份恢复。不要通过公开网盘传输配置和 profile。

安装 Python 和 Chromium 依赖：

```bash
cd /root/copy
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
chmod 700 data
chmod 600 config/config.yaml
```

如果使用 `.venv`，需要编辑 systemd 服务，把 `ExecStart` 从 `/usr/bin/python3` 改成 `/root/copy/.venv/bin/python`。服务单元默认假设项目路径为 `/root/copy`，迁移到其他路径时必须同步修改 `WorkingDirectory`、`ExecStart` 和登录脚本调用路径。

### 4. 安装并启动虚拟桌面

```bash
sudo cp /root/copy/systemd/binance-browser-desktop.service \
  /etc/systemd/system/binance-browser-desktop.service
sudo cp /root/copy/scripts/binance-browser-desktop \
  /usr/local/bin/binance-browser-desktop
sudo chmod 755 /usr/local/bin/binance-browser-desktop
sudo systemctl daemon-reload
sudo systemctl enable --now binance-browser-desktop
sudo systemctl is-active binance-browser-desktop
```

该服务的 VNC 只监听 `127.0.0.1:5900`。从自己的电脑建立隧道：

```bash
ssh -N -L 5900:127.0.0.1:5900 root@新服务器IP
```

VNC 客户端连接 `127.0.0.1:5900`。不要把 5900 端口直接开放到公网。

### 5. 在新服务器重新登录 Binance

推荐在新服务器重新建立 profile，不直接复制旧服务器的 `data/binance-browser-profile`：

```bash
cd /root/copy
./binance_browser_login.sh
```

在 VNC 中完成人工登录、验证码和二次验证，回到 SSH 窗口按 Enter。登录成功后执行只读检查：

```bash
cd /root/copy
DISPLAY=:99 .venv/bin/python -m src.binance_auth check \
  --profile data/binance-browser-profile \
  4956682966099962369 \
  5082904357337048064 \
  4982308422483092480
```

把命令中的 Portfolio ID 换成实际 `leaders` 配置。若没有使用虚拟环境，将 `.venv/bin/python` 换成 `python3`。

### 6. 迁移状态和配置

确认 `config/config.yaml` 中：

```yaml
binance_source:
  type: "hybrid"
  browser:
    profile_dir: "data/binance-browser-profile"
    headless: true
  session:
    refresh_interval_hours: 36
```

填写新的 Bitget API 凭证、Telegram 配置和带单员参数。若要保持旧服务的镜像状态，可在确认旧服务已停止后恢复状态文件：

```bash
mkdir -p ~/.copy-trader
cp /tmp/copy-trader-state.json ~/.copy-trader/state.json
chmod 600 ~/.copy-trader/state.json
```

若无法确认状态文件与当前 Bitget 实际仓位一致，不要盲目恢复；先备份它，并使用小额或暂停策略确认状态，避免重复开仓。

### 7. 安装并启动跟单服务

先做 Python 测试和配置检查。`validate` 不下单，但会访问 Bitget 账户和 Binance 仓位接口：

```bash
cd /root/copy
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m src.main validate --config config/config.yaml
```

安装服务：

```bash
sudo cp /root/copy/systemd/copy-trader.service \
  /etc/systemd/system/copy-trader.service
sudo systemctl daemon-reload
sudo systemctl enable copy-trader
sudo systemctl start copy-trader
sudo systemctl status copy-trader
sudo journalctl -u copy-trader -f
```

确认日志中只出现一台服务的轮询记录、浏览器认证成功和预期的带单员数量后，才认为迁移完成。新服务稳定后再清理旧服务器上的备份和旧 profile。

### 8. 回滚

如果新服务器出现认证、配置或下单逻辑问题：

```bash
sudo systemctl stop copy-trader
```

确认新服务完全停止后，再在旧服务器启动原服务：

```bash
sudo systemctl start copy-trader
sudo systemctl status copy-trader
```

不要让新旧服务同时连接同一个 Bitget 账户执行跟单。

## 正式运行前检查

⚠️ **警告**：切换到正式网后，系统会执行真实交易！

### 前置检查清单

- [ ] 本地单元测试全部通过
- [ ] 已用小额资金验证流程（确认 Bitget API 属于统一账户，且已授予 UTA 读写权限）
- [ ] 理解仓位计算公式和风险参数
- [ ] 已设置 Telegram 通知（强烈建议）
- [ ] 已配置黑名单（排除高风险币种）

### 启动步骤

1. **确认配置文件**

```yaml
execution:
  exchange: "bitget"
  api_key: "你的 Bitget UTA API Key"
  api_secret: "你的 Bitget UTA API Secret"
  api_passphrase: "创建 API Key 时设置的 Passphrase"
  sandbox: false                    # Bitget UTA 保留兼容，不代表测试网
```

2. **验证配置**

```bash
python3 -m src.main validate --config config/config.yaml
```

确认余额和连接正常。

3. **启动服务**

```bash
sudo systemctl start copy-trader
sudo journalctl -u copy-trader -f
```

5. **监控前 24 小时**

密切关注系统日志和 Telegram 通知，确保：
- 仓位计算正确
- 订单执行成功
- 无异常报错

---

## Cookie 管理

本节仅适用于 `binance_source.type: http`。`browser` 模式不需要把浏览器 Cookie 复制到配置文件；请按“故障恢复”处理重新登录。

### Cookie 过期检测

系统会自动检测 Cookie 失效：
- 连续 3 次 API 请求失败
- 自动暂停交易
- 发送 Telegram 通知

### 更新 Cookie

**步骤：**

1. 浏览器重新获取 Cookie（参考前面的教程）
2. 更新 `config/config.yaml` 中的 `cookie` 字段
3. 热重载配置（不需要停止服务）：

```bash
# 发送 SIGHUP 信号
sudo systemctl reload copy-trader

# 或者手动发送信号
sudo kill -HUP $(pgrep -f "src.main")
```

4. 检查日志，确认配置已重载：

```bash
sudo journalctl -u copy-trader -n 20
```

**预期日志：**
```
[INFO] Reload signal received, reloading config...
[INFO] Config reloaded successfully
[INFO] Coordinator resumed
```

---

## 故障恢复

### 浏览器模式认证失效

1. 查看状态和日志，确认服务已进入暂停状态：

```bash
sudo systemctl status copy-trader
sudo journalctl -u copy-trader -n 100 --no-pager
```

2. 停止跟单服务，避免登录过程中 profile 被占用：

```bash
sudo systemctl stop copy-trader
```

3. 保持 `binance-browser-desktop` 运行，通过 SSH 隧道和 VNC 登录：

```bash
cd /root/copy
./binance_browser_login.sh
```

4. 在 VNC 中完成验证，回到 SSH 窗口按 Enter；再用 `src.binance_auth check` 做只读检查。
5. 确认检查成功后启动跟单服务，并观察首轮同步日志：

```bash
sudo systemctl start copy-trader
sudo journalctl -u copy-trader -f
```

如果 profile 损坏，停止所有使用它的浏览器进程后先备份，再重新建立 profile。不要直接删除没有备份的登录 profile。

### HTTP 模式认证失效

重新获取 Cookie/header，更新 `config/config.yaml`，然后执行：

```bash
sudo systemctl reload copy-trader
```

连续三次鉴权失败会暂停跟单；网络错误和格式错误不会被当成清仓。

## 常见问题

### 1. 认证相关

**Q: 浏览器模式是否需要定期更新 Cookie？**

A: 正常情况下不需要。浏览器会从持久化 profile 自动携带和更新 Cookie/header。只有 Binance 要求重新登录、验证码、WAF 或二次验证时，才需要人工通过 VNC 恢复登录态。

**Q: 哪些 Cookie 字段是 HTTP 模式必需的？**

A: 必需字段：
- `p20t` - Session 令牌（最关键）
- `csrftoken` - CSRF 验证
- `bnc-uuid` - 设备标识

可选字段（不影响功能）：
- `aws-waf-token`
- `BNC_FV_KEY_T`

**Q: HTTP 模式的 Cookie 会过期吗？**

A: 会。Session 令牌（`p20t`）有效期约 7-30 天。过期后系统会自动暂停并通知。

**Q: 如何避免 Cookie 频繁过期？**

A: 保持浏览器登录状态，定期访问币安网站。

### 2. 配置相关

**Q: 带单员的总保证金如何获取？**

A: 在带单员详情页查看"合约账户总资产"（USDT）。

**Q: 我的保证金需要手动填写吗？**

A: 不需要。系统会自动从交易所 API 获取你的账户余额。

**Q: coefficient（系数）如何选择？**

A: 
- 风险偏好与带单员相同：`1.0`
- 更保守（减半仓位）：`0.5`
- 更激进（加倍仓位）：`2.0`

**Q: 如何跟单多个带单员？**

A: 在 `leaders` 列表中添加多个配置：

```yaml
leaders:
  - name: "Trader_A"
    portfolio_id: "123456"
    coefficient: 1.0
    total_margin: 50000
    enabled: true
    
  - name: "Trader_B"
    portfolio_id: "789012"
    coefficient: 2.0
    total_margin: 30000
    enabled: true
```

### 3. 交易所相关

**Q: Bitget 使用哪个账户和合约类型？**

A: 系统仅支持 Bitget 统一账户（UTA）的 USDT 永续合约，使用单向持仓和全仓模式。系统启动时会自动设置 `one_way_mode`；请在配置中填写 API Key、Secret 和 Passphrase。切换持仓模式前不能有持仓或挂单。

**Q: Bitget 不支持某些币种怎么办？**

A: 添加到黑名单：

```yaml
risk:
  blacklist:
    - "PEPEUSDT"
    - "SHIBUSDT"
```

**Q: 如何处理多带单员冲突（同时开相反方向）？**

A: 配置冲突策略：

```yaml
risk:
  conflict_resolution: "skip"  # 跳过冲突订单
```

### 4. Telegram 通知

**Q: Telegram chat_id 是什么？**

A: 接收消息的聊天 ID。获取方法：
1. 与 `@userinfobot` 对话
2. 它会返回你的 `chat_id`

**Q: 不配置 Telegram 可以运行吗？**

A: 可以。将 `enabled: false` 即可。但强烈建议启用，以便及时接收通知。

### 5. 运维相关

**Q: 如何查看系统状态？**

```bash
sudo systemctl status copy-trader
```

**Q: 如何重启服务？**

```bash
sudo systemctl restart copy-trader
```

**Q: 如何停止服务？**

```bash
sudo systemctl stop copy-trader
```

**Q: 日志文件太大怎么办？**

系统使用日志轮转，会自动清理旧日志。配置：

```yaml
logging:
  max_size_mb: 50       # 单个日志文件最大 50MB
  backup_count: 5       # 保留 5 个备份
```

### 6. 错误排查

**Error: "Insufficient margin"**

原因：账户余额不足。

解决：
- 充值
- 降低 coefficient（减小仓位）

**Error: "Invalid order"**

原因：订单参数不符合交易所规则（如最小下单量）。

解决：
- 检查币种是否支持
- 查看交易所规则
- 增加账户余额（订单量太小）

**Error: "Symbol not found"**

原因：交易所不支持该币种。

解决：
- 添加到黑名单
- 更换交易所

---

## 快速参考

### 常用命令

```bash
# 安装依赖
pip install -r requirements.txt

# 本地测试
python3 -m unittest discover -s tests -v

# 验证配置
python3 -m src.main validate

# 前台运行
python3 -m src.main run

# 服务管理
sudo systemctl start copy-trader
sudo systemctl stop copy-trader
sudo systemctl restart copy-trader
sudo systemctl status copy-trader
sudo systemctl reload copy-trader     # 热重载配置

# 查看日志
tail -f logs/app.log
sudo journalctl -u copy-trader -f
```

### 文件权限

```bash
# 保护配置文件（包含 API 凭证）
chmod 600 config/config.yaml
```

### 目录结构

```
/root/copy/
├── config/
│   ├── config.yaml              # 主配置文件
│   └── config.example.yaml      # 配置模板
├── tests/                       # 离线单元测试
├── src/
│   ├── main.py                  # CLI 入口
│   ├── coordinator.py           # 核心协调器
│   └── ...                      # 其他模块
├── logs/
│   └── app.log                  # 系统日志
├── systemd/
│   └── copy-trader.service      # Systemd 服务文件
├── data/
│   └── binance-browser-profile/ # 登录凭证，不提交 Git
├── README.md                    # 项目入口
└── USER_GUIDE.md                # 主操作和迁移手册
```

---

**需要帮助？** 查看 [常见问题](#常见问题) 部分。
