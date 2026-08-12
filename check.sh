#!/bin/bash
# 快速启动检查脚本

set -e

echo "=================================="
echo "币安合约跟单系统 - 启动前检查"
echo "=================================="
echo ""

# 检查 Python 版本
echo "1. 检查 Python3..."
python3 --version || { echo "错误: 未安装 Python3"; exit 1; }
echo "   ✓ Python3 已安装"
echo ""

# 检查依赖
echo "2. 检查依赖包..."
if python3 -c "import aiohttp; import yaml" 2>/dev/null; then
    echo "   ✓ aiohttp: $(python3 -c 'import aiohttp; print(aiohttp.__version__)')"
    echo "   ✓ pyyaml 已安装"
else
    echo "   ⚠ 缺少依赖："
    echo "     请手动运行: pip3 install -r requirements.txt"
fi
echo ""

# 检查主配置
echo "3. 检查主系统配置..."
if [ ! -f "config/config.yaml" ]; then
    echo "   ✗ config/config.yaml 不存在"
    echo "   执行: cp config/config.example.yaml config/config.yaml"
    echo "   并确保 chmod 600 config/config.yaml"
    exit 1
fi
echo "   ✓ config/config.yaml 存在"
echo ""

# 检查日志目录
echo "4. 检查日志目录..."
mkdir -p logs
echo "   ✓ 日志目录已创建"
echo ""

# 总结
echo "=================================="
echo "检查完成！"
echo "=================================="
echo ""
echo "接下来你可以："
echo "  1. 验证配置:     python3 -m src.main validate"
echo "  2. 启动系统:     python3 -m src.main run"
echo "  3. 运行测试:     python3 -m unittest discover -s tests -v"
echo ""
echo "详细说明请查看: USER_GUIDE.md"
echo ""
