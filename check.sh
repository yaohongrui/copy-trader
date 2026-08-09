#!/bin/bash
# 快速启动检查脚本

set -e

echo "=================================="
echo "币安合约跟单系统 - 启动前检查"
echo "=================================="
echo ""

# 检查 Python 版本
echo "1. 检查 Python 版本..."
python3 --version || { echo "错误: 未安装 Python3"; exit 1; }
echo "   ✓ Python3 已安装"
echo ""

# 检查依赖
echo "2. 检查依赖包..."
python3 -c "import aiohttp; import yaml" 2>/dev/null && {
    echo "   ✓ aiohttp: $(python3 -c 'import aiohttp; print(aiohttp.__version__)')"
    echo "   ✓ pyyaml 已安装"
} || {
    echo "   ✗ 缺少依赖，正在安装..."
    pip3 install -r requirements.txt
}
echo ""

# 检查 Sandbox 配置
echo "3. 检查 Sandbox 配置..."
if [ ! -f "sandbox/config.yaml" ]; then
    echo "   ✗ sandbox/config.yaml 不存在"
    echo "   执行: cp sandbox/config.example.yaml sandbox/config.yaml"
    exit 1
fi
echo "   ✓ sandbox/config.yaml 存在"
echo ""

# 检查主配置
echo "4. 检查主系统配置..."
if [ ! -f "config/config.yaml" ]; then
    echo "   ⚠ config/config.yaml 不存在（如仅测试 Sandbox 可忽略）"
else
    echo "   ✓ config/config.yaml 存在"
fi
echo ""

# 检查日志目录
echo "5. 检查日志目录..."
mkdir -p logs
mkdir -p sandbox/logs
echo "   ✓ 日志目录已创建"
echo ""

# 总结
echo "=================================="
echo "检查完成！"
echo "=================================="
echo ""
echo "接下来你可以："
echo ""
echo "【Sandbox 测试】（推荐首先执行）"
echo "  1. 测试 Cookie:  python3 sandbox/test_poller.py"
echo "  2. 测试计算:     python3 sandbox/test_sizer.py"
echo "  3. 实时监控:     python3 sandbox/monitor.py"
echo ""
echo "【主系统】（确认 Sandbox 测试正常后）"
echo "  4. 验证配置:     python3 -m src.main validate"
echo "  5. 启动系统:     python3 -m src.main run"
echo ""
echo "详细说明请查看: USER_GUIDE.md"
echo ""
