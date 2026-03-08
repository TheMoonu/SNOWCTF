#!/usr/bin/env bash

# ================================================
# Docker TLS 配置问题快速修复脚本
# ================================================
# 用途：修复 Docker TLS 配置导致的启动失败问题
# 用法：sudo ./fix_docker_tls.sh
# ================================================

set -euo pipefail

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

show_step() {
    echo -e "${GREEN}[步骤] $1${NC}"
}

show_info() {
    echo -e "${BLUE}[信息] $1${NC}"
}

show_success() {
    echo -e "${GREEN}[成功] $1${NC}"
}

show_error() {
    echo -e "${RED}[错误] $1${NC}"
}

show_warning() {
    echo -e "${YELLOW}[警告] $1${NC}"
}

# 检查 root 权限
if [ "$(id -u)" -ne 0 ]; then
    show_error "此脚本需要 root 权限运行，请使用 sudo"
    exit 1
fi

echo ""
echo "========================================="
echo -e "${GREEN}Docker TLS 配置修复脚本${NC}"
echo "========================================="
echo ""

# 1. 检查当前状态
show_step "检查 Docker 当前状态..."
systemctl status docker --no-pager -l || true
echo ""

# 2. 检查 daemon.json
show_step "检查 daemon.json 配置..."
if [ -f /etc/docker/daemon.json ]; then
    show_info "当前 daemon.json 内容："
    cat /etc/docker/daemon.json
    echo ""
    
    # JSON 格式验证
    if command -v python3 &> /dev/null; then
        if python3 -m json.tool /etc/docker/daemon.json > /dev/null 2>&1; then
            show_success "JSON 格式正确"
        else
            show_error "JSON 格式错误！"
            python3 -m json.tool /etc/docker/daemon.json || true
        fi
    fi
fi
echo ""

# 3. 创建/修复 systemd override 配置
show_step "修复 systemd 配置..."

mkdir -p /etc/systemd/system/docker.service.d

cat > /etc/systemd/system/docker.service.d/override.conf <<'EOF'
[Service]
# 清空原有的 ExecStart
ExecStart=
# 重新定义 ExecStart（不带 -H 参数）
ExecStart=/usr/bin/dockerd
EOF

show_info "已创建 override.conf:"
cat /etc/systemd/system/docker.service.d/override.conf
echo ""

# 4. 重载并重启
show_step "重载 systemd 并重启 Docker..."
systemctl daemon-reload
show_success "systemd 配置已重载"

# 尝试启动
if systemctl restart docker; then
    sleep 3
    if systemctl is-active --quiet docker; then
        show_success "Docker 服务启动成功！"
        echo ""
        
        # 显示监听端口
        show_info "Docker 监听端口："
        ss -tuln | grep dockerd || netstat -tuln | grep dockerd || true
        echo ""
        
        # 显示 Docker 信息
        show_info "Docker 版本信息："
        docker version
        
    else
        show_error "Docker 服务启动失败"
        show_info "查看详细错误："
        journalctl -xeu docker --no-pager -n 30
    fi
else
    show_error "Docker 重启命令失败"
    echo ""
    show_info "详细错误日志："
    journalctl -xeu docker --no-pager -n 50
    echo ""
    
    # 提供回滚方案
    show_warning "如需回滚到原始配置："
    echo "  1. 移除 override 配置："
    echo "     sudo rm -f /etc/systemd/system/docker.service.d/override.conf"
    echo "  2. 恢复原始 daemon.json（如果有备份）："
    echo "     sudo cp /etc/docker/daemon.json.backup.* /etc/docker/daemon.json"
    echo "  3. 重启 Docker："
    echo "     sudo systemctl daemon-reload"
    echo "     sudo systemctl restart docker"
fi

echo ""
echo "========================================="

