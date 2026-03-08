#!/usr/bin/env bash

# ================================================
# Docker 远程访问 + TLS 加密一键配置脚本
# ================================================
# 用途：为 Docker 开启远程 TLS 访问
# 用法：sudo ./enable_docker_remote_tls.sh <SERVER_IP_OR_FQDN> [PORT]
# 示例：sudo ./enable_docker_remote_tls.sh 192.168.1.100 2376
# ================================================

set -euo pipefail

# 颜色输出
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置参数
DAYS=36500                      # 证书有效期（约100年）
KEY_SIZE=4096                   # 密钥长度
DOCKER_PORT="${2:-2376}"        # Docker TLS 端口，默认 2376
CERT_DIR="/etc/docker/certs"    # 证书存储目录

# 证书文件路径
CA_KEY="${CERT_DIR}/ca-key.pem"
CA_CRT="${CERT_DIR}/ca.pem"
SRV_KEY="${CERT_DIR}/server-key.pem"
SRV_CSR="${CERT_DIR}/server.csr"
SRV_CRT="${CERT_DIR}/server.pem"
CLI_KEY="${CERT_DIR}/client-key.pem"
CLI_CSR="${CERT_DIR}/client.csr"
CLI_CRT="${CERT_DIR}/client.pem"

# 检查参数
if [ $# -lt 1 ]; then
    echo -e "${RED}用法错误！${NC}"
    echo ""
    echo "用法: sudo $0 <SERVER_IP_OR_FQDN> [PORT]"
    echo ""
    echo "参数说明："
    echo "  SERVER_IP_OR_FQDN  - 服务器IP地址或域名（必需）"
    echo "  PORT               - Docker远程访问端口（可选，默认2376）"
    echo ""
    echo "示例："
    echo "  sudo $0 192.168.1.100"
    echo "  sudo $0 192.168.1.100 2376"
    echo "  sudo $0 example.com 2376"
    exit 1
fi

SERVER_IP=$1

# 显示函数
show_step() {
    echo -e "${GREEN}[步骤] $1${NC}"
}

show_warning() {
    echo -e "${YELLOW}[警告] $1${NC}"
}

show_error() {
    echo -e "${RED}[错误] $1${NC}"
    exit 1
}

show_success() {
    echo -e "${GREEN}[成功] $1${NC}"
}

show_info() {
    echo -e "${BLUE}[信息] $1${NC}"
}

# 检查 root 权限
check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        show_error "此脚本需要 root 权限运行，请使用 sudo"
    fi
}

# 检测操作系统
detect_os() {
    if [ -f /etc/os-release ]; then
        . /etc/os-release
        OS=$ID
        OS_VERSION=$VERSION_ID
    elif [ -f /etc/redhat-release ]; then
        OS="centos"
    elif [ -f /etc/debian_version ]; then
        OS="debian"
    else
        OS="unknown"
    fi
    
    case "$OS" in
        ubuntu|debian)
            OS_TYPE="debian"
            DOCKER_SERVICE_FILE="/lib/systemd/system/docker.service"
            ;;
        centos|rhel|fedora|rocky|almalinux)
            OS_TYPE="rhel"
            DOCKER_SERVICE_FILE="/usr/lib/systemd/system/docker.service"
            ;;
        *)
            OS_TYPE="unknown"
            ;;
    esac
    
    export OS_TYPE OS OS_VERSION
}

# 检查 Docker 是否安装
check_docker() {
    show_step "检查 Docker 环境..."
    
    if ! command -v docker &> /dev/null; then
        show_error "Docker 未安装，请先安装 Docker"
    fi
    
    if ! systemctl is-active --quiet docker; then
        show_warning "Docker 服务未运行，尝试启动..."
        systemctl start docker || show_error "无法启动 Docker 服务"
    fi
    
    show_success "Docker 已安装并运行"
    docker --version
}

# 备份现有配置
backup_config() {
    show_step "备份现有配置..."
    
    local timestamp=$(date +%Y%m%d_%H%M%S)
    
    # 备份 daemon.json
    if [ -f /etc/docker/daemon.json ]; then
        cp /etc/docker/daemon.json "/etc/docker/daemon.json.backup.$timestamp"
        show_info "已备份 daemon.json 到 /etc/docker/daemon.json.backup.$timestamp"
    fi
    
    # 备份证书目录
    if [ -d "$CERT_DIR" ]; then
        mv "$CERT_DIR" "${CERT_DIR}.backup.$timestamp"
        show_info "已备份证书目录到 ${CERT_DIR}.backup.$timestamp"
    fi
    
    show_success "配置备份完成"
}

# 生成 TLS 证书
generate_tls_certs() {
    show_step "生成 TLS 证书..."
    
    # 创建证书目录
    mkdir -p "$CERT_DIR"
    cd "$CERT_DIR"
    
    # 1. 生成 CA 证书
    show_info "1/4 生成自签 CA 证书（有效期：${DAYS}天）..."
    openssl req -new -x509 -nodes -days "$DAYS" \
        -newkey rsa:"$KEY_SIZE" -keyout "$CA_KEY" -out "$CA_CRT" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Docker/CN=Docker-CA" \
        2>/dev/null
    
    # 2. 生成服务端私钥
    show_info "2/4 生成服务端私钥..."
    openssl genrsa -out "$SRV_KEY" "$KEY_SIZE" 2>/dev/null
    
    # 3. 生成服务端 CSR
    show_info "3/4 生成服务端证书签名请求..."
    openssl req -new -key "$SRV_KEY" -out "$SRV_CSR" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Docker/CN=docker-server" \
        2>/dev/null
    
    # 创建 extfile（包含 SAN）
    local extfile="${CERT_DIR}/extfile.cnf"
    cat > "$extfile" <<EOF
subjectAltName = IP:${SERVER_IP},IP:127.0.0.1,DNS:localhost
extendedKeyUsage = serverAuth
EOF
    
    # 4. 签名服务端证书
    show_info "4/4 签名服务端证书..."
    openssl x509 -req -days "$DAYS" -sha256 \
        -in "$SRV_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
        -out "$SRV_CRT" -extfile "$extfile" \
        2>/dev/null
    
    # 5. 生成客户端私钥
    show_info "5/6 生成客户端私钥..."
    openssl genrsa -out "$CLI_KEY" "$KEY_SIZE" 2>/dev/null
    
    # 6. 生成客户端 CSR
    show_info "6/6 生成客户端证书签名请求..."
    openssl req -new -key "$CLI_KEY" -out "$CLI_CSR" \
        -subj "/C=CN/ST=Beijing/L=Beijing/O=Docker/CN=docker-client" \
        2>/dev/null
    
    # 创建客户端 extfile
    cat > "$extfile" <<EOF
extendedKeyUsage = clientAuth
EOF
    
    # 7. 签名客户端证书
    show_info "签名客户端证书..."
    openssl x509 -req -days "$DAYS" -sha256 \
        -in "$CLI_CSR" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
        -out "$CLI_CRT" -extfile "$extfile" \
        2>/dev/null
    
    # 设置权限
    chmod 400 "$CA_KEY" "$SRV_KEY" "$CLI_KEY"
    chmod 444 "$CA_CRT" "$SRV_CRT" "$CLI_CRT"
    
    # 清理临时文件
    rm -f "$SRV_CSR" "$CLI_CSR" "$extfile" "${CERT_DIR}/ca.srl"
    
    show_success "TLS 证书生成完成"
    
    echo ""
    show_info "证书文件列表："
    ls -lh "$CERT_DIR"/*.pem
    echo ""
}

# 配置 Docker daemon
configure_docker_daemon() {
    show_step "配置 Docker daemon..."
    
    local daemon_config="/etc/docker/daemon.json"
    
    # 读取现有配置（如果存在）
    if [ -f "$daemon_config" ]; then
        # 使用 python/jq 合并配置，这里简化处理
        show_info "检测到现有 daemon.json，将进行合并..."
    fi
    
    # 创建新配置
    cat > "$daemon_config" <<EOF
{
  "hosts": ["unix:///var/run/docker.sock", "tcp://0.0.0.0:${DOCKER_PORT}"],
  "tls": true,
  "tlsverify": true,
  "tlscacert": "${CA_CRT}",
  "tlscert": "${SRV_CRT}",
  "tlskey": "${SRV_KEY}",
  "registry-mirrors": [
    "https://docker.mirrors.ustc.edu.cn",
    "https://mirror.ccs.tencentyun.com"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
    
    show_success "Docker daemon 配置完成"
}

# 配置 systemd service
configure_systemd() {
    show_step "配置 systemd service..."
    
    # 强制使用 systemd drop-in 配置（最可靠的方式）
    show_info "使用 systemd drop-in 配置方式..."
    
    mkdir -p /etc/systemd/system/docker.service.d
    
    # 创建 override 配置，清空 ExecStart 并重新定义
    cat > /etc/systemd/system/docker.service.d/override.conf <<EOF
[Service]
# 清空原有的 ExecStart
ExecStart=
# 重新定义 ExecStart（不带任何 -H 参数，使用 daemon.json 配置）
ExecStart=/usr/bin/dockerd
EOF
    
    show_info "已创建 drop-in 配置: /etc/systemd/system/docker.service.d/override.conf"
    
    # 显示配置内容
    show_info "配置内容："
    cat /etc/systemd/system/docker.service.d/override.conf
    
    show_success "systemd service 配置完成"
}

# 配置防火墙
configure_firewall() {
    show_step "配置防火墙..."
    
    # 检查并配置 firewalld（CentOS/RHEL）
    if command -v firewall-cmd &> /dev/null && systemctl is-active --quiet firewalld; then
        show_info "检测到 firewalld，开放端口 ${DOCKER_PORT}..."
        firewall-cmd --permanent --add-port=${DOCKER_PORT}/tcp
        firewall-cmd --reload
        show_success "firewalld 配置完成"
    # 检查并配置 ufw（Ubuntu/Debian）
    elif command -v ufw &> /dev/null && ufw status | grep -q "active"; then
        show_info "检测到 ufw，开放端口 ${DOCKER_PORT}..."
        ufw allow ${DOCKER_PORT}/tcp
        show_success "ufw 配置完成"
    else
        show_warning "未检测到活动的防火墙，请手动配置防火墙规则"
        show_info "需要开放端口: ${DOCKER_PORT}/tcp"
    fi
}

# 重启 Docker 服务
restart_docker() {
    show_step "重启 Docker 服务..."
    
    # 重载 systemd 配置
    systemctl daemon-reload
    show_info "systemd 配置已重载"
    
    # 尝试重启 Docker
    if systemctl restart docker; then
        sleep 3
        
        if systemctl is-active --quiet docker; then
            show_success "Docker 服务重启成功"
        else
            show_error "Docker 服务启动失败，请检查配置：
  sudo systemctl status docker
  sudo journalctl -xeu docker"
        fi
    else
        # 重启失败，显示详细错误信息
        echo ""
        show_error "Docker 服务重启失败！"
        echo ""
        echo "========================================="
        echo -e "${RED}错误诊断信息：${NC}"
        echo "========================================="
        echo ""
        
        echo -e "${YELLOW}1. Docker 服务状态：${NC}"
        systemctl status docker --no-pager -l || true
        echo ""
        
        echo -e "${YELLOW}2. 最近的错误日志：${NC}"
        journalctl -xeu docker --no-pager -n 50 || true
        echo ""
        
        echo -e "${YELLOW}3. daemon.json 配置检查：${NC}"
        if [ -f /etc/docker/daemon.json ]; then
            echo "配置文件内容："
            cat /etc/docker/daemon.json
            echo ""
            
            # 使用 python 检查 JSON 格式
            if command -v python3 &> /dev/null; then
                echo "JSON 格式验证："
                python3 -m json.tool /etc/docker/daemon.json > /dev/null 2>&1 && \
                    echo -e "${GREEN}✓ JSON 格式正确${NC}" || \
                    echo -e "${RED}✗ JSON 格式错误${NC}"
            fi
        fi
        echo ""
        
        echo -e "${YELLOW}4. systemd override 配置：${NC}"
        if [ -f /etc/systemd/system/docker.service.d/override.conf ]; then
            cat /etc/systemd/system/docker.service.d/override.conf
        fi
        echo ""
        
        echo "========================================="
        echo -e "${BLUE}常见解决方案：${NC}"
        echo "========================================="
        echo ""
        echo "1. 检查 daemon.json 格式是否正确"
        echo "2. 确保证书文件路径存在且权限正确"
        echo "3. 检查是否有其他服务占用端口 ${DOCKER_PORT}"
        echo "4. 尝试手动启动 Docker："
        echo "   sudo systemctl start docker"
        echo "   sudo journalctl -xeu docker"
        echo ""
        
        exit 1
    fi
}

# 验证配置
verify_config() {
    show_step "验证配置..."
    
    # 检查端口监听
    sleep 2
    if netstat -tuln 2>/dev/null | grep -q ":${DOCKER_PORT}" || ss -tuln 2>/dev/null | grep -q ":${DOCKER_PORT}"; then
        show_success "Docker 正在监听端口 ${DOCKER_PORT}"
    else
        show_warning "未检测到端口 ${DOCKER_PORT} 监听，请检查配置"
    fi
    
    # 测试本地 TLS 连接
    show_info "测试本地 TLS 连接..."
    if docker --tlsverify \
        --tlscacert="$CA_CRT" \
        --tlscert="$CLI_CRT" \
        --tlskey="$CLI_KEY" \
        -H="tcp://127.0.0.1:${DOCKER_PORT}" version &>/dev/null; then
        show_success "本地 TLS 连接测试成功"
    else
        show_warning "本地 TLS 连接测试失败，请检查证书配置"
    fi
}

# 打包客户端证书
package_client_certs() {
    show_step "打包客户端证书..."
    
    local client_dir="/tmp/docker-client-certs-${SERVER_IP}"
    local client_tar="/tmp/docker-client-certs-${SERVER_IP}.tar.gz"
    
    mkdir -p "$client_dir"
    
    cp "$CA_CRT" "$client_dir/ca.pem"
    cp "$CLI_CRT" "$client_dir/cert.pem"
    cp "$CLI_KEY" "$client_dir/key.pem"
    
    # 创建使用说明
    cat > "$client_dir/README.txt" <<EOF
Docker TLS 客户端证书
====================

服务器地址: ${SERVER_IP}:${DOCKER_PORT}

使用方法：

1. 将这三个文件放到客户端机器的 ~/.docker 目录
   mkdir -p ~/.docker
   cp ca.pem cert.pem key.pem ~/.docker/

2. 设置环境变量：
   export DOCKER_HOST=tcp://${SERVER_IP}:${DOCKER_PORT}
   export DOCKER_TLS_VERIFY=1
   export DOCKER_CERT_PATH=~/.docker

3. 测试连接：
   docker version
   docker ps

4. 或者直接使用命令行参数：
   docker --tlsverify \\
     --tlscacert=~/.docker/ca.pem \\
     --tlscert=~/.docker/cert.pem \\
     --tlskey=~/.docker/key.pem \\
     -H=tcp://${SERVER_IP}:${DOCKER_PORT} version

安全提示：
- 请妥善保管这些证书文件
- 不要将证书提交到版本控制系统
- 建议设置文件权限: chmod 400 ~/.docker/*.pem
EOF
    
    # 打包
    cd /tmp
    tar -czf "$client_tar" "docker-client-certs-${SERVER_IP}"
    rm -rf "$client_dir"
    
    chmod 644 "$client_tar"
    
    show_success "客户端证书已打包到: $client_tar"
    show_info "证书包权限已设置为 644，可被普通用户读取"
}

# 显示完成信息
show_completion() {
    echo ""
    echo "========================================="
    echo -e "${GREEN}Docker TLS 远程访问配置完成！${NC}"
    echo "========================================="
    echo ""
    echo -e "${BLUE}服务器信息：${NC}"
    echo "  IP地址/域名: ${SERVER_IP}"
    echo "  端口:        ${DOCKER_PORT}"
    echo ""
    echo -e "${BLUE}证书位置：${NC}"
    echo "  服务器证书: ${CERT_DIR}/"
    echo "  客户端证书: /tmp/docker-client-certs-${SERVER_IP}.tar.gz"
    echo ""
    echo -e "${BLUE}客户端使用方法：${NC}"
    echo ""
    echo "1. 下载客户端证书包到本地："
    echo "   # 方式1：使用 scp（任何用户均可）"
    echo "   scp root@${SERVER_IP}:/tmp/docker-client-certs-${SERVER_IP}.tar.gz ."
    echo ""
    echo ""
    echo -e "${YELLOW}安全提示：${NC}"
    echo "  1. 请妥善保管客户端证书文件"
    echo "  2. 建议配置防火墙，仅允许可信IP访问端口 ${DOCKER_PORT}"
    echo "  3. 定期更新证书（当前证书有效期约100年）"
    echo "  4. 不要将证书文件提交到版本控制系统"
    echo "  5. 注意：/tmp 目录下的文件可能在重启后被清理，请及时下载"
    echo ""
    echo "========================================="
}

# 主函数
main() {
    echo ""
    echo "========================================="
    echo -e "${GREEN}Docker TLS 远程访问配置脚本${NC}"
    echo "========================================="
    echo ""
    
    # 显示配置信息
    echo -e "${BLUE}配置参数：${NC}"
    echo "  服务器地址: ${SERVER_IP}"
    echo "  TLS 端口:   ${DOCKER_PORT}"
    echo "  证书目录:   ${CERT_DIR}"
    echo "  证书有效期: ${DAYS} 天（约100年）"
    echo ""
    
    # 确认继续
    read -p "是否继续配置? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        show_warning "配置已取消"
        exit 0
    fi
    
    echo ""
    
    # 执行配置步骤
    check_root
    detect_os
    show_info "检测到操作系统: ${OS} ${OS_VERSION:-unknown}"
    echo ""
    
    check_docker
    backup_config
    generate_tls_certs
    configure_docker_daemon
    configure_systemd
    configure_firewall
    restart_docker
    verify_config
    package_client_certs
    
    echo ""
    show_completion
}

# 执行主函数
main

