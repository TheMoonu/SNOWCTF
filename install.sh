#!/bin/bash

# 网络安全综合学习平台 首次安装脚本
# 用途：从 base 目录加载镜像并初始化服务

# 设置颜色输出
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 配置变量
# 自动获取脚本所在目录（支持任意安装路径）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SCRIPT_DIR}"
BASE_DIR="${INSTALL_DIR}/base"

# 显示步骤信息
show_step() {
    echo -e "${GREEN}[步骤] $1${NC}"
}

# 显示警告信息
show_warning() {
    echo -e "${YELLOW}[警告] $1${NC}"
}

# 显示错误信息
show_error() {
    echo -e "${RED}[错误] $1${NC}"
    exit 1
}

# 显示成功信息
show_success() {
    echo -e "${GREEN}[成功] $1${NC}"
}

# 显示信息
show_info() {
    echo -e "${BLUE}[信息] $1${NC}"
}

# 检查端口是否被占用
check_port() {
    local port=$1
    if command -v ss &> /dev/null; then
        # 优先使用 ss 命令（更快）
        ss -tuln | grep -q ":${port} "
    elif command -v netstat &> /dev/null; then
        # 备用 netstat 命令
        netstat -tuln | grep -q ":${port} "
    elif command -v lsof &> /dev/null; then
        # 备用 lsof 命令
        lsof -i ":${port}" -sTCP:LISTEN >/dev/null 2>&1
    else
        # 如果都没有，尝试用 nc 测试（最后的手段）
        if command -v nc &> /dev/null; then
            nc -z 127.0.0.1 ${port} >/dev/null 2>&1
        else
            # 无法检测，假设端口未被占用
            return 1
        fi
    fi
    return $?
}

# 获取端口占用进程信息
get_port_process() {
    local port=$1
    local process_info=""
    
    if command -v lsof &> /dev/null; then
        process_info=$(lsof -i ":${port}" -sTCP:LISTEN 2>/dev/null | grep LISTEN | awk '{print $1" (PID: "$2")"}' | head -n 1)
    elif command -v ss &> /dev/null; then
        process_info=$(ss -tlnp | grep ":${port} " | grep -oP 'pid=\d+' | head -n 1)
    elif command -v netstat &> /dev/null; then
        process_info=$(netstat -tlnp 2>/dev/null | grep ":${port} " | awk '{print $7}' | head -n 1)
    fi
    
    if [ -n "$process_info" ]; then
        echo "$process_info"
    else
        echo "未知进程"
    fi
}

# 检查必需端口
check_required_ports() {
    show_step "检查端口占用情况..."
    
    # 默认端口配置
    DEFAULT_HTTP_PORT=80
    DEFAULT_HTTPS_PORT=443
    
    # 检查结果标志
    PORTS_OK=true
    
    # 检查 HTTP 端口 (80)
    if check_port ${DEFAULT_HTTP_PORT}; then
        show_warning "端口 ${DEFAULT_HTTP_PORT} (HTTP) 已被占用"
        PROCESS_INFO=$(get_port_process ${DEFAULT_HTTP_PORT})
        show_info "占用进程: ${PROCESS_INFO}"
        PORTS_OK=false
    else
        show_success "端口 ${DEFAULT_HTTP_PORT} (HTTP) 可用"
    fi
    
    # 检查 HTTPS 端口 (443)
    if check_port ${DEFAULT_HTTPS_PORT}; then
        show_warning "端口 ${DEFAULT_HTTPS_PORT} (HTTPS) 已被占用"
        PROCESS_INFO=$(get_port_process ${DEFAULT_HTTPS_PORT})
        show_info "占用进程: ${PROCESS_INFO}"
        PORTS_OK=false
    else
        show_success "端口 ${DEFAULT_HTTPS_PORT} (HTTPS) 可用"
    fi
    
    # 如果有端口被占用，询问用户
    if [ "$PORTS_OK" = false ]; then
        echo ""
        show_warning "检测到端口占用！"
        echo ""
        echo -e "${YELLOW}您有以下选择：${NC}"
        echo "  1. 停止占用端口的服务，然后使用默认端口继续安装"
        echo "  2. 使用自定义端口继续安装"
        echo "  3. 取消安装"
        echo ""
        
        read -p "请选择 (1/2/3): " -n 1 -r
        echo
        echo ""
        
        case $REPLY in
            1)
                show_info "请手动停止占用端口的服务，然后重新运行安装脚本"
                echo ""
                show_info "常见端口释放方法："
                echo "  查看占用进程: sudo lsof -i :80"
                echo "  停止 Nginx: sudo systemctl stop nginx"
                echo "  停止 Apache: sudo systemctl stop apache2 或 httpd"
                echo ""
                exit 0
                ;;
            2)
                configure_custom_ports
                ;;
            3)
                show_warning "安装已取消"
                exit 0
                ;;
            *)
                show_error "无效选择，安装已取消"
                ;;
        esac
    else
        show_success "所有端口检查通过"
        export CUSTOM_HTTP_PORT=${DEFAULT_HTTP_PORT}
        export CUSTOM_HTTPS_PORT=${DEFAULT_HTTPS_PORT}
    fi
    
    echo ""
}

# 配置自定义端口
configure_custom_ports() {
    show_step "配置自定义端口..."
    echo ""
    
    # HTTP 端口
    while true; do
        read -p "请输入 HTTP 端口 [默认 80, 推荐 8080]: " HTTP_PORT
        HTTP_PORT=${HTTP_PORT:-8080}
        
        if ! [[ "$HTTP_PORT" =~ ^[0-9]+$ ]] || [ "$HTTP_PORT" -lt 1 ] || [ "$HTTP_PORT" -gt 65535 ]; then
            show_error "无效的端口号，请输入 1-65535 之间的数字"
            continue
        fi
        
        if check_port ${HTTP_PORT}; then
            show_warning "端口 ${HTTP_PORT} 已被占用，请选择其他端口"
            continue
        fi
        
        show_success "HTTP 端口设置为: ${HTTP_PORT}"
        break
    done
    
    # HTTPS 端口
    while true; do
        read -p "请输入 HTTPS 端口 [默认 443, 推荐 8443]: " HTTPS_PORT
        HTTPS_PORT=${HTTPS_PORT:-8443}
        
        if ! [[ "$HTTPS_PORT" =~ ^[0-9]+$ ]] || [ "$HTTPS_PORT" -lt 1 ] || [ "$HTTPS_PORT" -gt 65535 ]; then
            show_error "无效的端口号，请输入 1-65535 之间的数字"
            continue
        fi
        
        if [ "$HTTPS_PORT" -eq "$HTTP_PORT" ]; then
            show_warning "HTTPS 端口不能与 HTTP 端口相同"
            continue
        fi
        
        if check_port ${HTTPS_PORT}; then
            show_warning "端口 ${HTTPS_PORT} 已被占用，请选择其他端口"
            continue
        fi
        
        show_success "HTTPS 端口设置为: ${HTTPS_PORT}"
        break
    done
    
    echo ""
    show_success "端口配置完成"
    echo ""
    echo -e "${BLUE}端口配置摘要：${NC}"
    echo "  HTTP 端口:  ${HTTP_PORT}"
    echo "  HTTPS 端口: ${HTTPS_PORT}"
    echo ""
    
    export CUSTOM_HTTP_PORT=${HTTP_PORT}
    export CUSTOM_HTTPS_PORT=${HTTPS_PORT}
}

# 检测操作系统类型
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
            ;;
        centos|rhel|fedora|rocky|almalinux)
            OS_TYPE="rhel"
            ;;
        *)
            OS_TYPE="unknown"
            ;;
    esac
    
    export OS_TYPE OS OS_VERSION
}

# 安装Docker（根据操作系统自动选择）
install_docker() {
    show_step "开始安装 Docker..."
    
    # 检测操作系统
    detect_os
    
    if [ "$OS_TYPE" == "unknown" ]; then
        show_error "无法识别操作系统类型，请手动安装 Docker
        
查看安装指引:
  $0 --help-docker"
    fi
    
    show_info "检测到操作系统: $OS ${OS_VERSION:-unknown}"
    
    case "$OS_TYPE" in
        debian)
            install_docker_debian
            ;;
        rhel)
            install_docker_rhel
            ;;
        *)
            show_error "不支持的操作系统: $OS"
            ;;
    esac
}

# 在 Debian/Ubuntu 系统上安装 Docker
install_docker_debian() {
    show_info "使用阿里云镜像源安装 Docker（适用于 Ubuntu/Debian）..."
    
    # 卸载旧版本
    show_info "卸载旧版本 Docker（如果存在）..."
    apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
    
    # 更新包索引
    show_info "更新软件包索引..."
    apt-get update || show_error "apt-get update 失败，请检查网络连接"
    
    # 安装依赖
    show_info "安装必要依赖..."
    apt-get install -y \
        ca-certificates \
        curl \
        gnupg \
        lsb-release || show_error "安装依赖失败"
    
    # 使用官方一键安装脚本（阿里云镜像）
    show_info "下载并执行 Docker 安装脚本..."
    curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun
    
    if [ $? -eq 0 ]; then
        show_success "Docker 安装成功"
    else
        show_error "Docker 安装失败，请查看上方错误信息"
    fi
    
    # 启动 Docker 服务
    show_info "启动 Docker 服务..."
    systemctl start docker
    systemctl enable docker
    
    # 验证安装
    if docker --version &> /dev/null; then
        show_success "Docker 安装验证成功"
        docker --version
    else
        show_error "Docker 安装验证失败"
    fi
}

# 在 CentOS/RHEL 系统上安装 Docker
install_docker_rhel() {
    show_info "使用阿里云镜像源安装 Docker（适用于 CentOS/RHEL）..."
    
    # 卸载旧版本
    show_info "卸载旧版本 Docker（如果存在）..."
    yum remove -y docker \
        docker-client \
        docker-client-latest \
        docker-common \
        docker-latest \
        docker-latest-logrotate \
        docker-logrotate \
        docker-engine 2>/dev/null || true
    
    # 安装依赖
    show_info "安装必要依赖..."
    yum install -y yum-utils || show_error "安装依赖失败"
    
    # 添加 Docker 仓库（阿里云镜像）
    show_info "添加 Docker 仓库..."
    yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo || show_error "添加仓库失败"
    
    # 安装 Docker
    show_info "安装 Docker..."
    yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    
    if [ $? -eq 0 ]; then
        show_success "Docker 安装成功"
    else
        show_error "Docker 安装失败，请查看上方错误信息"
    fi
    
    # 启动 Docker 服务
    show_info "启动 Docker 服务..."
    systemctl start docker
    systemctl enable docker
    
    # 验证安装
    if docker --version &> /dev/null; then
        show_success "Docker 安装验证成功"
        docker --version
    else
        show_error "Docker 安装验证失败"
    fi
}

# 获取可用的 Docker Compose 命令（兼容 V1 和 V2）
get_compose_command() {
    # 优先使用 Docker Compose V2（docker compose）
    if docker compose version &> /dev/null 2>&1; then
        echo "docker compose"
    # 其次使用独立版本（docker-compose）
    elif command -v docker-compose &> /dev/null; then
        echo "docker-compose"
    else
        echo ""
    fi
}

# 安装 docker-compose（如果需要）
install_docker_compose() {
    show_step "检查 Docker Compose..."
    
    # 优先检查 Docker Compose V2（docker compose 命令）
    if docker compose version &> /dev/null 2>&1; then
        show_success "Docker Compose V2 已内置，无需额外安装"
        docker compose version
        return 0
    fi
    
    # 检查独立版本的 docker-compose
    if command -v docker-compose &> /dev/null; then
        show_success "docker-compose 独立版本已安装"
        docker-compose --version
        return 0
    fi
    
    # 如果都没有，提示安装独立版本（用于老版本 Docker）
    show_warning "未检测到 Docker Compose，尝试安装独立版本..."
    show_info "下载 docker-compose（使用国内镜像）..."
    
    # 下载最新版本的 docker-compose
    curl -L "https://get.daocloud.io/docker/compose/releases/download/v2.24.0/docker-compose-$(uname -s)-$(uname -m)" \
        -o /usr/local/bin/docker-compose
    
    if [ $? -eq 0 ]; then
        chmod +x /usr/local/bin/docker-compose
        show_success "docker-compose 独立版本安装成功"
        docker-compose --version
    else
        show_error "docker-compose 安装失败
        
请检查网络连接或手动安装：
  sudo curl -L \"https://get.daocloud.io/docker/compose/releases/download/v2.24.0/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose"
    fi
}

# 生成随机密码（避免使用bash特殊字符）
generate_password() {
    # 仅使用字母和数字，避免特殊字符导致的问题
    cat /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 20
}

# 生成Redis密码（仅使用字母和数字，避免URL编码问题）
generate_redis_password() {
    # 仅使用字母和数字，避免特殊字符在URL中导致问题
    cat /dev/urandom | tr -dc 'A-Za-z0-9' | head -c 24
}


# 显示Docker安装指引
show_docker_install_guide() {
    echo ""
    echo "========================================="
    echo -e "${YELLOW}Docker 安装指引${NC}"
    echo "========================================="
    echo ""
    echo -e "${BLUE}方式一：使用官方一键安装脚本（国内推荐使用阿里云镜像）${NC}"
    echo ""
    echo "Ubuntu/Debian:"
    echo "  curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun"
    echo ""
    echo "或手动安装（阿里云镜像源）:"
    echo "  curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | sudo apt-key add -"
    echo "  sudo add-apt-repository \"deb [arch=amd64] https://mirrors.aliyun.com/docker-ce/linux/ubuntu \$(lsb_release -cs) stable\""
    echo "  sudo apt-get update"
    echo "  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin"
    echo ""
    echo "CentOS/RHEL:"
    echo "  sudo yum install -y yum-utils"
    echo "  sudo yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo"
    echo "  sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin"
    echo ""
    echo -e "${BLUE}方式二：离线安装（推荐用于无网络环境）${NC}"
    echo ""
    echo "1. 在有网络的机器上下载安装包："
    echo "   https://download.docker.com/linux/static/stable/x86_64/"
    echo ""
    echo "2. 解压并安装："
    echo "   tar xzvf docker-*.tgz"
    echo "   sudo cp docker/* /usr/bin/"
    echo "   sudo dockerd &"
    echo ""
    echo -e "${BLUE}Docker Compose 说明${NC}"
    echo ""
    echo "Docker 20.10+ 版本已内置 Docker Compose V2（推荐）"
    echo "  验证命令: docker compose version"
    echo "  使用方式: docker compose up -d"
    echo ""
    echo "如果您使用老版本 Docker，可安装独立版本:"
    echo "  sudo curl -L \"https://get.daocloud.io/docker/compose/releases/download/v2.24.0/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose"
    echo "  sudo chmod +x /usr/local/bin/docker-compose"
    echo ""
    echo "========================================="
    echo ""
}

# 检查Docker是否安装
check_docker() {
    show_step "检查Docker环境..."
    
    # 检测操作系统
    detect_os
    
    # 检查Docker是否安装
    if ! command -v docker &> /dev/null; then
        show_warning "Docker 未安装！"
        echo ""
        echo -e "${YELLOW}检测到操作系统: ${OS} ${OS_VERSION:-unknown}${NC}"
        echo ""
        echo "本脚本可以自动为您安装最新版本的 Docker。"
        echo ""
        
        # 询问是否自动安装
        read -p "是否现在自动安装 Docker? (y/n): " -n 1 -r
        echo
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            # 自动安装Docker
            install_docker
            
            # 检查/安装 docker-compose
            echo ""
            install_docker_compose
            
            echo ""
            show_success "Docker 安装完成，继续执行安装流程..."
            echo ""
            sleep 2
        else
            show_error "Docker 未安装，无法继续。

请手动安装 Docker 后再运行此脚本。

快速安装命令（使用阿里云镜像）:
  curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun

安装完成后启动Docker:
  sudo systemctl start docker
  sudo systemctl enable docker

查看详细安装指引请运行:
  $0 --help-docker
"
        fi
    fi
    
    show_success "Docker 已安装"
    
    # 检查Docker服务是否运行
    if ! docker info &> /dev/null 2>&1; then
        show_warning "Docker服务未运行，尝试启动..."
        if systemctl start docker 2>/dev/null; then
            sleep 2
            if docker info &> /dev/null 2>&1; then
                show_success "Docker服务启动成功"
            else
                show_error "Docker服务启动失败，请手动检查: sudo systemctl status docker"
            fi
        else
            show_error "无法启动Docker服务，请检查：
1. 是否有root权限（需要sudo）
2. Docker是否正确安装
3. 运行: sudo systemctl status docker 查看详细错误"
        fi
    fi
    
    # 检查 Docker Compose（优先检查 V2 内置版本）
    HAS_COMPOSE=0
    
    # 优先检查 Docker Compose V2（内置在 Docker 20.10+ 中）
    if docker compose version &> /dev/null 2>&1; then
        show_success "Docker Compose V2 已安装（内置版本，推荐）"
        HAS_COMPOSE=1
    # 其次检查独立版本的 docker-compose
    elif command -v docker-compose &> /dev/null; then
        show_success "docker-compose 已安装（独立版本）"
        HAS_COMPOSE=1
    fi
    
    if [ $HAS_COMPOSE -eq 0 ]; then
        show_warning "未检测到 Docker Compose"
        echo ""
        echo -e "${YELLOW}说明：${NC}"
        echo "  - Docker 20.10+ 版本自带 Docker Compose V2"
        echo "  - 使用命令: docker compose（推荐）"
        echo "  - 老版本需要安装独立的 docker-compose"
        echo ""
        
        # 询问是否安装docker-compose
        read -p "是否现在检查/安装 Docker Compose? (y/n): " -n 1 -r
        echo
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            install_docker_compose
        else
            show_error "Docker Compose 未安装，无法继续。

如果您使用的是 Docker 20.10+ 版本，Docker Compose V2 应该已经内置。
请尝试运行：
  docker compose version

如果上述命令失败，请手动安装独立版本：
  sudo curl -L \"https://get.daocloud.io/docker/compose/releases/download/v2.24.0/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose
  sudo chmod +x /usr/local/bin/docker-compose

查看完整安装指引:
  $0 --help-docker
"
        fi
    fi
    
    # 显示Docker信息
    echo ""
    show_info "Docker 版本信息："
    docker --version
    
    # 优先显示 Docker Compose V2（内置版本）
    if docker compose version &> /dev/null 2>&1; then
        docker compose version
    elif command -v docker-compose &> /dev/null; then
        docker-compose --version
    fi
    echo ""
    
    show_success "Docker环境检查通过"
}

# 检查镜像文件
check_images() {
    show_step "检查镜像文件..."
    
    cd "${BASE_DIR}" || show_error "base目录不存在: ${BASE_DIR}"
    
    # 检查必需的镜像tar文件
    MISSING_FILES=0
    
    if [ ! -f "postgres.tar" ]; then
        show_warning "缺少 postgres.tar"
        MISSING_FILES=$((MISSING_FILES + 1))
    fi
    
    if [ ! -f "redis.tar" ]; then
        show_warning "缺少 redis.tar"
        MISSING_FILES=$((MISSING_FILES + 1))
    fi
    
    if [ ! -f "nginx.tar" ]; then
        show_warning "缺少 nginx.tar"
        MISSING_FILES=$((MISSING_FILES + 1))
    fi
    
    # 自动查找 secsnow 开头的 tar 文件（更灵活，不固定文件名）
    SECSNOW_TAR_FILE=$(ls secsnow*.tar 2>/dev/null | head -n 1)
    if [ -z "$SECSNOW_TAR_FILE" ]; then
        show_warning "未找到 secsnow*.tar 格式的镜像文件"
        MISSING_FILES=$((MISSING_FILES + 1))
    else
        show_info "检测到 SecSnow 镜像文件: ${SECSNOW_TAR_FILE}"
        export SECSNOW_TAR_FILE
    fi
    
    if [ $MISSING_FILES -gt 0 ]; then
        echo ""
        show_error "缺少 $MISSING_FILES 个镜像文件，请确保以下文件存在于 ${BASE_DIR}:
  - postgres.tar (PostgreSQL 17 镜像)
  - redis.tar (Redis 8.4.0 镜像)
  - nginx.tar (Nginx 镜像)
  - secsnow*.tar (SecSnow Web 镜像，例如: secsnow_cty_sy_sp1.tar)"
    fi
    
    show_success "所有镜像文件检查完成"
    ls -lh *.tar
    echo ""
}

# 加载Docker镜像
load_images() {
    show_step "加载Docker镜像..."
    
    cd "${BASE_DIR}" || show_error "无法进入base目录"
    
    # 加载PostgreSQL镜像并获取镜像名
    show_info "加载 PostgreSQL 镜像..."
    POSTGRES_LOADED=$(docker load -i postgres.tar 2>&1)
    if [ $? -eq 0 ]; then
        # 提取镜像名称，格式类似: Loaded image: postgres:17-bookworm
        # 使用 head -n 1 确保只取第一个标签（避免多标签镜像导致问题）
        POSTGRES_IMAGE_NAME=$(echo "$POSTGRES_LOADED" | grep -oP 'Loaded image: \K.*' | head -n 1 || echo "postgres:17-bookworm")
        show_success "PostgreSQL 镜像加载成功: $POSTGRES_IMAGE_NAME"
    else
        show_error "PostgreSQL 镜像加载失败"
    fi
    
    # 加载Redis镜像并获取镜像名
    show_info "加载 Redis 镜像..."
    REDIS_LOADED=$(docker load -i redis.tar 2>&1)
    if [ $? -eq 0 ]; then
        REDIS_IMAGE_NAME=$(echo "$REDIS_LOADED" | grep -oP 'Loaded image: \K.*' | head -n 1 || echo "redis:8.4.0")
        show_success "Redis 镜像加载成功: $REDIS_IMAGE_NAME"
    else
        show_error "Redis 镜像加载失败"
    fi
    
    # 加载 PgBouncer 镜像并获取镜像名
    show_info "加载 PgBouncer 镜像..."
    if [ -f "pgbouncer.tar" ]; then
        PGBOUNCER_LOADED=$(docker load -i pgbouncer.tar 2>&1)
        if [ $? -eq 0 ]; then
            PGBOUNCER_IMAGE_NAME=$(echo "$PGBOUNCER_LOADED" | grep -oP 'Loaded image: \K.*' | head -n 1 || echo "edoburu/pgbouncer:latest")
            show_success "PgBouncer 镜像加载成功: $PGBOUNCER_IMAGE_NAME"
        else
            show_warning "PgBouncer 镜像加载失败，将使用默认镜像"
            PGBOUNCER_IMAGE_NAME="edoburu/pgbouncer:latest"
        fi
    else
        show_warning "未找到 pgbouncer.tar 文件，将使用默认镜像"
        PGBOUNCER_IMAGE_NAME="edoburu/pgbouncer:latest"
    fi
    
    # 加载Nginx镜像并获取镜像名
    show_info "加载 Nginx 镜像..."
    NGINX_LOADED=$(docker load -i nginx.tar 2>&1)
    if [ $? -eq 0 ]; then
        NGINX_IMAGE_NAME=$(echo "$NGINX_LOADED" | grep -oP 'Loaded image: \K.*' | head -n 1 || echo "nginx:alpine")
        show_success "Nginx 镜像加载成功: $NGINX_IMAGE_NAME"
    else
        show_error "Nginx 镜像加载失败"
    fi
    
    # 加载SecSnow Web镜像并获取镜像名（使用动态检测的文件名）
    show_info "加载 SecSnow Web 镜像: ${SECSNOW_TAR_FILE}..."
    SECSNOW_LOADED=$(docker load -i "${SECSNOW_TAR_FILE}" 2>&1)
    if [ $? -eq 0 ]; then
        SECSNOW_IMAGE_NAME=$(echo "$SECSNOW_LOADED" | grep -oP 'Loaded image: \K.*' | head -n 1 || echo "secsnow:secure")
        SECSNOW_IMAGE_COUNT=$(echo "$SECSNOW_LOADED" | grep -c 'Loaded image:' || echo "1")
        if [ "$SECSNOW_IMAGE_COUNT" -gt 1 ]; then
            show_success "SecSnow Web 镜像加载成功: $SECSNOW_IMAGE_NAME (检测到 $SECSNOW_IMAGE_COUNT 个标签，使用第一个)"
        else
            show_success "SecSnow Web 镜像加载成功: $SECSNOW_IMAGE_NAME"
        fi
    else
        show_error "SecSnow Web 镜像加载失败"
    fi
    
    echo ""
    show_success "所有镜像加载完成"
    
    show_info "已加载的镜像列表："
    docker images | grep -E "postgres|pgbouncer|redis|nginx|secsnow" || true
    echo ""
    
    export LOADED_POSTGRES_IMAGE="$POSTGRES_IMAGE_NAME"
    export LOADED_PGBOUNCER_IMAGE="$PGBOUNCER_IMAGE_NAME"
    export LOADED_REDIS_IMAGE="$REDIS_IMAGE_NAME"
    export LOADED_NGINX_IMAGE="$NGINX_IMAGE_NAME"
    export LOADED_SECSNOW_IMAGE="$SECSNOW_IMAGE_NAME"
}

# 从 Docker 仓库拉取镜像
pull_images_from_registry() {
    show_step "从 Docker 仓库拉取镜像..."
    
    REGISTRY_POSTGRES_IMAGE="${REGISTRY_POSTGRES_IMAGE:-postgres:17-bookworm}"
    REGISTRY_PGBOUNCER_IMAGE="${REGISTRY_PGBOUNCER_IMAGE:-edoburu/pgbouncer:latest}"
    REGISTRY_REDIS_IMAGE="${REGISTRY_REDIS_IMAGE:-redis:8.4.0}"
    REGISTRY_NGINX_IMAGE="${REGISTRY_NGINX_IMAGE:-nginx:alpine}"
    
    if [ -z "$REGISTRY_SECSNOW_IMAGE" ]; then
        show_error "使用 --pull 模式时，必须使用 --secsnow-image 参数指定 SecSnow 镜像
        
示例:
  $0 --pull --secsnow-image registry.example.com/secsnow:v1.0.0"
    fi
    
    show_info "将拉取以下镜像："
    echo "  PostgreSQL: ${REGISTRY_POSTGRES_IMAGE}"
    echo "  PgBouncer:  ${REGISTRY_PGBOUNCER_IMAGE}"
    echo "  Redis:      ${REGISTRY_REDIS_IMAGE}"
    echo "  Nginx:      ${REGISTRY_NGINX_IMAGE}"
    echo "  SecSnow:    ${REGISTRY_SECSNOW_IMAGE}"
    echo ""
    
    for item in \
        "PostgreSQL:${REGISTRY_POSTGRES_IMAGE}" \
        "PgBouncer:${REGISTRY_PGBOUNCER_IMAGE}" \
        "Redis:${REGISTRY_REDIS_IMAGE}" \
        "Nginx:${REGISTRY_NGINX_IMAGE}" \
        "SecSnow:${REGISTRY_SECSNOW_IMAGE}"; do
        NAME="${item%%:*}"
        IMAGE="${item#*:}"
        show_info "拉取 ${NAME} 镜像: ${IMAGE}..."
        if docker pull "${IMAGE}"; then
            show_success "${NAME} 镜像拉取成功"
        else
            show_error "${NAME} 镜像拉取失败，请检查镜像名称和网络连接"
        fi
    done
    
    echo ""
    show_success "所有镜像拉取完成"
    show_info "已拉取的镜像列表："
    docker images | grep -E "postgres|pgbouncer|redis|nginx|secsnow" || true
    echo ""
    
    export LOADED_POSTGRES_IMAGE="$REGISTRY_POSTGRES_IMAGE"
    export LOADED_PGBOUNCER_IMAGE="$REGISTRY_PGBOUNCER_IMAGE"
    export LOADED_REDIS_IMAGE="$REGISTRY_REDIS_IMAGE"
    export LOADED_NGINX_IMAGE="$REGISTRY_NGINX_IMAGE"
    export LOADED_SECSNOW_IMAGE="$REGISTRY_SECSNOW_IMAGE"
}


# 生成环境配置文件
generate_env() {
    show_step "生成环境配置文件..."
    
    cd "${INSTALL_DIR}" || show_error "无法进入安装目录"
    
    # 生成随机密码
    DB_PASSWORD=$(generate_password)
    REDIS_PASSWORD=$(generate_redis_password)
    SECRET_KEY=$(generate_password)$(generate_password)$(generate_password)
    
    TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
    
    SECSNOW_VERSION=$(echo "${LOADED_SECSNOW_IMAGE}" | grep -oP ':[^:]+$' | sed 's/^://' || echo "unknown")
    if [ -z "$SECSNOW_VERSION" ] || [ "$SECSNOW_VERSION" = "unknown" ]; then
        SECSNOW_VERSION="v1.0.0"
    fi
    
    cat > .env << ENV_EOF
# ================================================
# 平台配置文件
# ================================================
# 自动生成时间: ${TIMESTAMP}
# 说明：
# 1. 此文件由安装脚本自动生成
# 2. 敏感信息（密码、密钥）已自动生成随机值
# 3. 修改配置后需要重启服务: docker compose restart
# ================================================

# ================================================
# Docker 镜像版本配置
# ================================================
SECSNOW_VERSION=${SECSNOW_VERSION}
POSTGRES_IMAGE=${LOADED_POSTGRES_IMAGE:-postgres:17-bookworm}
PGBOUNCER_IMAGE=${LOADED_PGBOUNCER_IMAGE:-edoburu/pgbouncer:latest}
REDIS_IMAGE=${LOADED_REDIS_IMAGE:-redis:8.4.0}
NGINX_IMAGE=${LOADED_NGINX_IMAGE:-nginx:alpine}
SECSNOW_IMAGE=${LOADED_SECSNOW_IMAGE:-secsnow:secure}

# ================================================
# PostgreSQL 数据库配置
# ================================================
POSTGRES_DB=secsnow
POSTGRES_USER=secsnow
POSTGRES_PASSWORD=${DB_PASSWORD}

# ================================================
# Redis 配置
# ================================================
REDIS_PASSWORD=${REDIS_PASSWORD}

# ================================================
# Django 应用配置
# ================================================
SNOW_SECRET_KEY=${SECRET_KEY}
SNOW_DEBUG=False
SNOW_ALLOWED_HOSTS=*
# CSRF信任来源（如通过域名代理，需配置为实际域名，含协议+域名+端口）
#SNOW_CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
SNOW_PROTOCOL_HTTPS=http
# 邮箱验证方式（none/optional/mandatory）
SNOW_ACCOUNT_EMAIL_VERIFICATION=none
# 数据加密密钥（用于加密敏感信息）强烈建议修改为随机字符串
ENCRYPTION_KEY=SecSnowEncryptKey20251211
#SNOW_SIMPLEUI_HOME_TITLE=SECSNOW
#SNOW_SIMPLEUI_LOGO=https://www.secsnow.cn/static/blog/img/logo.svg

# ================================================
# 端口配置
# ================================================
NGINX_HTTP_PORT=${CUSTOM_HTTP_PORT:-80}
NGINX_HTTPS_PORT=${CUSTOM_HTTPS_PORT:-443}

# ================================================
# 数据持久化目录配置
# ================================================
POSTGRES_DATA_DIR=./db/postgres
REDIS_DATA_DIR=./redis/data
WEB_STATIC_DIR=./web/static
WEB_MEDIA_DIR=./web/media
WEB_LOG_DIR=./web/log
WEB_WHOOSH_DIR=./web/whoosh_index
NGINX_CONF_DIR=./nginx/conf.d
NGINX_SSL_DIR=./nginx/ssl
NGINX_LOG_DIR=./web/log/nginx

# ================================================
# Celery 配置
# ================================================
CELERY_WORKER_CONCURRENCY=50

# ================================================
# Gunicorn 配置
# ================================================
GUNICORN_WORKERS=4
GUNICORN_WORKER_CONNECTIONS=500
GUNICORN_TIMEOUT=200

# ================================================
# 高级配置
# ================================================
NETWORK_NAME=secsnow-network
CONTAINER_PREFIX=secsnow
TZ=Asia/Shanghai
ENV_EOF

    show_success ".env 配置文件生成完成"
    
    cat > .credentials << EOF
# ================================================
# 安装凭证信息
# ================================================
# 生成时间: $(date '+%Y-%m-%d %H:%M:%S')
# ================================================

平台版本:
  SecSnow:    ${SECSNOW_VERSION}

Docker镜像:
  PostgreSQL: ${LOADED_POSTGRES_IMAGE:-postgres:17-bookworm}
  Redis:      ${LOADED_REDIS_IMAGE:-redis:8.4.0}
  Nginx:      ${LOADED_NGINX_IMAGE:-nginx:alpine}
  SecSnow:    ${LOADED_SECSNOW_IMAGE:-secsnow:secure}

数据库配置:
  数据库名: secsnow
  用户名:   secsnow
  密码:     ${DB_PASSWORD}

Redis配置:
  密码:     ${REDIS_PASSWORD}

Django配置:
  SECRET_KEY: ${SECRET_KEY}

端口配置:
  HTTP端口:  ${CUSTOM_HTTP_PORT:-80}
  HTTPS端口: ${CUSTOM_HTTPS_PORT:-443}

# ================================================
# 重要提示
# ================================================
# 1. 请妥善保存此文件
# 2. 首次登录后建议修改管理员密码
# 3. 生产环境建议修改所有默认密码
# 4. 此文件权限已设置为 600（仅所有者可读写）
# ================================================
EOF
    
    chmod 600 .credentials
    
    show_info "凭证信息已保存到: ${INSTALL_DIR}/.credentials"
    echo ""
}

# 优化 Redis 系统配置
optimize_redis_system() {
    show_step "优化 Redis 系统配置..."
    
    # 检查是否有 root 权限
    if [ "$EUID" -ne 0 ]; then
        show_warning "需要 root 权限来优化系统配置，跳过优化"
        show_info "建议手动执行以下命令（需要 sudo）："
        echo "  echo 'vm.overcommit_memory = 1' >> /etc/sysctl.conf"
        echo "  sysctl vm.overcommit_memory=1"
        echo "  echo 'net.core.somaxconn = 511' >> /etc/sysctl.conf"
        echo "  sysctl net.core.somaxconn=511"
        return 0
    fi
    
    # 1. 启用内存超额分配
    show_info "配置内存超额分配..."
    if ! grep -q "vm.overcommit_memory" /etc/sysctl.conf 2>/dev/null; then
        echo "vm.overcommit_memory = 1" >> /etc/sysctl.conf
        sysctl vm.overcommit_memory=1
        show_success "内存超额分配已启用"
    else
        show_info "内存超额分配已配置，跳过"
    fi
    
    # 2. 增加 TCP backlog
    show_info "配置 TCP backlog..."
    if ! grep -q "net.core.somaxconn" /etc/sysctl.conf 2>/dev/null; then
        echo "net.core.somaxconn = 511" >> /etc/sysctl.conf
        sysctl net.core.somaxconn=511
        show_success "TCP backlog 已优化"
    else
        show_info "TCP backlog 已配置，跳过"
    fi
    
    # 3. 禁用透明大页（可选，提升性能）
    show_info "禁用透明大页..."
    if [ -f /sys/kernel/mm/transparent_hugepage/enabled ]; then
        echo never > /sys/kernel/mm/transparent_hugepage/enabled
        show_success "透明大页已禁用"
    fi
    
    show_success "Redis 系统优化完成"
    echo ""
}

# 启动服务
start_services() {
    show_step "启动Docker服务..."
    
    cd "${INSTALL_DIR}" || show_error "无法进入安装目录"
    
    show_info "创建数据目录..."
    mkdir -p db/postgres redis/data web/media web/static web/log web/log/nginx web/whoosh_index nginx/ssl
    show_success "数据目录创建完成"
    
    COMPOSE_CMD=$(get_compose_command)
    if [ -z "$COMPOSE_CMD" ]; then
        show_error "无法找到 Docker Compose 命令"
    fi
    
    show_info "使用命令: $COMPOSE_CMD"
    show_info "启动所有服务..."
    if $COMPOSE_CMD up -d; then
        show_success "服务启动成功"
    else
        show_error "服务启动失败，请检查日志"
    fi
    
    show_step "等待服务完全启动..."
    sleep 10
    
    show_info "服务状态："
    $COMPOSE_CMD ps
    echo ""
}

# 执行数据库迁移
run_migrations() {
    show_step "执行数据库初始化..."
    
    cd "${INSTALL_DIR}" || show_error "无法进入安装目录"
    
    # 等待数据库完全就绪
    show_info "等待数据库就绪..."
    sleep 5
    
    # 执行数据库迁移
    show_info "创建数据库表..."
    docker exec secsnow-web python manage.py makemigrations || show_warning "makemigrations 执行有警告"
    docker exec secsnow-web python manage.py migrate || show_error "数据库迁移失败"
    
    # 收集静态文件
    show_info "收集静态文件..."
    docker exec secsnow-web python manage.py collectstatic --noinput || show_error "收集静态文件失败"
    
    # 功能初始化
    show_info "初始化功能模块..."
    docker exec secsnow-web python manage.py init_license_modules || show_warning "功能初始化有警告"
    
    # 网站初始化
    show_info "初始化网站数据..."
    docker exec secsnow-web python manage.py init_site_data || show_warning "网站初始化有警告"
    
    show_success "数据库初始化完成"
}

# 创建管理员账户
create_admin_user() {
    if [ "${CREATE_ADMIN}" == "yes" ]; then
        show_step "创建管理员账户..."
        
        # 生成随机密码
        ADMIN_PASSWORD=$(generate_password)
        
        # 使用 createsuperuser 命令创建管理员（非交互式）
        docker exec -e DJANGO_SUPERUSER_USERNAME=admin \
                    -e DJANGO_SUPERUSER_EMAIL=admin@admin.com \
                    -e DJANGO_SUPERUSER_PASSWORD="${ADMIN_PASSWORD}" \
                    secsnow-web python manage.py createsuperuser --noinput 2>&1
        
        if [ $? -eq 0 ]; then
            show_success "管理员账户创建完成"
            
            # 保存管理员信息
            cat >> .credentials << EOF

管理员账户:
  用户名: admin
  邮箱:   admin@admin.com
  密码:   ${ADMIN_PASSWORD}
EOF
            
            echo ""
            echo "========================================="
            echo -e "${GREEN}管理员账户信息：${NC}"
            echo "  用户名: admin"
            echo "  邮箱:   admin@admin.com"
            echo -e "  密码:   ${YELLOW}${ADMIN_PASSWORD}${NC}"
            echo "========================================="
            echo -e "${YELLOW}请妥善保存以上密码信息！${NC}"
            echo ""
        else
            show_warning "管理员账户创建可能失败（可能已存在）"
        fi
    else
        show_info "跳过管理员账户创建（如需创建，请使用参数: yes）"
    fi
}


# 显示安装完成信息
show_completion() {
    show_success "安装完成！"
    
    COMPOSE_CMD=$(get_compose_command)
    
    echo ""
    echo "========================================="
    echo -e "${GREEN}安装信息汇总${NC}"
    echo "========================================="
    echo ""
    echo -e "${BLUE}服务访问:${NC}"
    if [ "${CUSTOM_HTTP_PORT:-80}" = "80" ]; then
        echo "  Web服务: http://您的IP地址"
    else
        echo "  Web服务: http://您的IP地址:${CUSTOM_HTTP_PORT}"
    fi
    echo ""
    echo -e "${BLUE}管理命令:${NC}"
    echo "  查看服务状态:"
    echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD ps"
    echo ""
    echo "  查看Web日志:"
    echo "    docker logs -f secsnow-web"
    echo ""
    echo "  重启服务:"
    echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD restart"
    echo ""
    echo "  停止服务:"
    echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD down"
    echo ""
    echo -e "${BLUE}重要文件:${NC}"
    echo "  配置文件: ${INSTALL_DIR}/.env"
    echo "  凭证信息: ${INSTALL_DIR}/.credentials"
    echo ""
    echo -e "${YELLOW}提示:${NC}"
    echo "  1. 请妥善保存 .credentials 文件中的密码信息"
    echo "  2. 建议修改默认管理员密码"
    echo "  3. 生产环境请配置防火墙规则"
    echo "  4. 网站首页内容、页脚内容、导航栏内容，请根据实际情况在后台管理对应模块进行修改！"
    
    echo "========================================="
}

# 显示完整的Docker安装指引
show_full_docker_guide() {
    echo ""
    echo "========================================="
    echo -e "${GREEN}Docker 完整安装指引${NC}"
    echo "========================================="
    echo ""
    
    echo -e "${BLUE}═══ Ubuntu/Debian 系统 ═══${NC}"
    echo ""
    echo -e "${YELLOW}方式1: 使用阿里云镜像源（推荐）${NC}"
    echo ""
    echo "curl -fsSL https://get.docker.com | bash -s docker --mirror Aliyun"
    echo ""
    echo -e "${YELLOW}方式2: 手动安装（清华源）${NC}"
    echo ""
    echo "# 卸载旧版本"
    echo "sudo apt-get remove docker docker-engine docker.io containerd runc"
    echo ""
    echo "# 安装依赖"
    echo "sudo apt-get update"
    echo "sudo apt-get install ca-certificates curl gnupg lsb-release"
    echo ""
    echo "# 添加清华源GPG密钥"
    echo "curl -fsSL https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg"
    echo ""
    echo "# 设置清华源仓库"
    echo "echo \"deb [arch=\$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://mirrors.tuna.tsinghua.edu.cn/docker-ce/linux/ubuntu \$(lsb_release -cs) stable\" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null"
    echo ""
    echo "# 安装Docker"
    echo "sudo apt-get update"
    echo "sudo apt-get install docker-ce docker-ce-cli containerd.io docker-compose-plugin"
    echo ""
    
    echo -e "${BLUE}═══ CentOS/RHEL 系统 ═══${NC}"
    echo ""
    echo -e "${YELLOW}使用阿里云镜像源${NC}"
    echo ""
    echo "# 卸载旧版本"
    echo "sudo yum remove docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine"
    echo ""
    echo "# 安装依赖"
    echo "sudo yum install -y yum-utils"
    echo ""
    echo "# 添加阿里云Docker仓库"
    echo "sudo yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo"
    echo ""
    echo "# 安装Docker"
    echo "sudo yum install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin"
    echo ""
    
    echo -e "${BLUE}═══ 启动Docker服务 ═══${NC}"
    echo ""
    echo "sudo systemctl start docker"
    echo "sudo systemctl enable docker"
    echo ""
    echo "# 验证安装"
    echo "docker --version"
    echo "sudo docker run hello-world"
    echo ""
    
    echo -e "${BLUE}═══ Docker Compose 说明 ═══${NC}"
    echo ""
    echo -e "${GREEN}Docker 20.10+ 版本已内置 Docker Compose V2（推荐）${NC}"
    echo ""
    echo "验证是否已安装："
    echo "  docker compose version"
    echo ""
    echo "使用方式（注意是两个单词，有空格）："
    echo "  docker compose up -d"
    echo "  docker compose down"
    echo "  docker compose ps"
    echo ""
    echo -e "${YELLOW}仅在老版本 Docker 中需要安装独立的 docker-compose:${NC}"
    echo ""
    echo "# 使用国内镜像（DaoCloud）"
    echo "sudo curl -L \"https://get.daocloud.io/docker/compose/releases/download/v2.24.0/docker-compose-\$(uname -s)-\$(uname -m)\" -o /usr/local/bin/docker-compose"
    echo "sudo chmod +x /usr/local/bin/docker-compose"
    echo "docker-compose --version"
    echo ""
    echo -e "${BLUE}注意：${NC}"
    echo "  - 'docker compose' 是新版本（V2），推荐使用"
    echo "  - 'docker-compose' 是老版本（V1），逐步被淘汰"
    echo ""
    
    echo -e "${BLUE}═══ 配置Docker镜像加速（可选但推荐）═══${NC}"
    echo ""
    echo "sudo mkdir -p /etc/docker"
    echo "sudo tee /etc/docker/daemon.json <<-'EOF'"
    echo "{"
    echo "  \"registry-mirrors\": ["
    echo "    \"https://docker.mirrors.ustc.edu.cn\","
    echo "    \"https://mirror.ccs.tencentyun.com\","
    echo "    \"https://registry.docker-cn.com\""
    echo "  ]"
    echo "}"
    echo "EOF"
    echo "sudo systemctl daemon-reload"
    echo "sudo systemctl restart docker"
    echo ""
    echo "========================================="
    echo ""
}

# 显示帮助信息
show_help() {
    echo ""
    echo "SecSnow 安装脚本"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help              显示此帮助信息"
    echo "  --help-docker           显示 Docker 完整安装指引"
    echo "  --pull                  从 Docker 仓库拉取镜像（而非本地 tar 文件）"
    echo "  --postgres-image <镜像>  指定 PostgreSQL 镜像（配合 --pull 使用）"
    echo "                          默认: postgres:17-bookworm"
    echo "  --pgbouncer-image <镜像> 指定 PgBouncer 镜像（配合 --pull 使用）"
    echo "                          默认: edoburu/pgbouncer:latest"
    echo "  --redis-image <镜像>     指定 Redis 镜像（配合 --pull 使用）"
    echo "                          默认: redis:8.4.0"
    echo "  --nginx-image <镜像>     指定 Nginx 镜像（配合 --pull 使用）"
    echo "                          默认: nginx:alpine"
    echo "  --secsnow-image <镜像>   指定 SecSnow 镜像（配合 --pull 使用，必需）"
    echo "  yes/no                  是否创建管理员账户（默认: yes）"
    echo ""
    echo "示例:"
    echo "  $0                      交互式安装（使用本地 tar 文件）"
    echo "  $0 no                   安装但不创建管理员账户"
    echo "  $0 --pull --secsnow-image registry.example.com/secsnow:v1.0.0"
    echo "                          从仓库拉取镜像进行安装（使用默认依赖镜像）"
    echo "  $0 --pull --secsnow-image myregistry/secsnow:latest \\"
    echo "     --postgres-image postgres:16 \\"
    echo "     --redis-image redis:7"
    echo "                          从仓库拉取指定版本镜像"
    echo ""
    echo "安装模式:"
    echo "  1. 本地模式（默认）: 从 ${BASE_DIR} 目录加载 tar 文件"
    echo "  2. 仓库模式（--pull）: 从 Docker 仓库拉取指定镜像"
    echo ""
    echo "注意事项:"
    echo "  - 本地模式需要准备好所有镜像的 tar 文件"
    echo "  - 仓库模式需要网络连接且可以访问 Docker 仓库"
    echo "  - 仓库模式必须使用 --secsnow-image 指定 SecSnow 镜像"
    echo ""
}

# 检查是否已安装
check_existing_installation() {
    show_step "检查现有安装..."
    
    # 检查标志文件
    if [ -f "${INSTALL_DIR}/.installed" ]; then
        INSTALL_TIME=$(grep "^安装时间:" "${INSTALL_DIR}/.installed" | cut -d':' -f2- | xargs 2>/dev/null || echo '未知')
        INSTALL_MODE=$(grep "^安装模式:" "${INSTALL_DIR}/.installed" | cut -d':' -f2 | xargs 2>/dev/null || echo '未知')
        
        show_error "检测到系统已经安装过！

安装标志文件: ${INSTALL_DIR}/.installed
安装时间: ${INSTALL_TIME}
安装方式: ${INSTALL_MODE}

⚠️  重复安装可能导致数据丢失！

如果需要：
  - 更新系统: 使用 update.sh 脚本
  - 重新安装: 先运行以下命令清理:
    cd ${INSTALL_DIR}
    docker compose down -v
    rm -f .installed .env .credentials
    rm -rf db/postgres redis/data web/media web/log
    或者将安装目录下的所有文件和目录删除，然后重新拉取配置文件
    然后再运行安装脚本

如果确定要强制重新安装，请先删除 .installed 文件:
  rm -f ${INSTALL_DIR}/.installed
"
    fi
    
    # 检查是否有运行中的容器
    if docker ps -a --format '{{.Names}}' | grep -q "^secsnow-"; then
        show_warning "检测到 SecSnow 相关容器正在运行或存在！"
        echo ""
        echo "运行中的容器:"
        docker ps -a --filter "name=secsnow-" --format "  - {{.Names}} ({{.Status}})"
        echo ""
        
        read -p "是否停止并删除现有容器继续安装? (y/n): " -n 1 -r
        echo
        
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            show_info "停止并删除现有容器..."
            cd "${INSTALL_DIR}" 2>/dev/null || true
            
            # 获取 compose 命令
            COMPOSE_CMD=$(get_compose_command)
            if [ -n "$COMPOSE_CMD" ] && [ -f "docker-compose.yml" ]; then
                $COMPOSE_CMD down -v
            else
                # 手动删除容器
                docker stop $(docker ps -aq --filter "name=secsnow-") 2>/dev/null || true
                docker rm $(docker ps -aq --filter "name=secsnow-") 2>/dev/null || true
            fi
            
            show_success "现有容器已清理"
        else
            show_error "安装已取消。请先手动清理现有安装。"
        fi
    fi
    
    show_success "安装检查通过"
}

# 主函数
main() {
    USE_REGISTRY=false
    REGISTRY_POSTGRES_IMAGE=""
    REGISTRY_PGBOUNCER_IMAGE=""
    REGISTRY_REDIS_IMAGE=""
    REGISTRY_NGINX_IMAGE=""
    REGISTRY_SECSNOW_IMAGE=""
    CREATE_ADMIN=""
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            --help-docker)
                show_full_docker_guide
                exit 0
                ;;
            --pull)
                USE_REGISTRY=true
                shift
                ;;
            --postgres-image)
                if [ -n "$2" ] && [ "${2:0:1}" != "-" ]; then
                    REGISTRY_POSTGRES_IMAGE="$2"
                    shift 2
                else
                    show_error "--postgres-image 参数需要指定镜像名称"
                fi
                ;;
            --pgbouncer-image)
                if [ -n "$2" ] && [ "${2:0:1}" != "-" ]; then
                    REGISTRY_PGBOUNCER_IMAGE="$2"
                    shift 2
                else
                    show_error "--pgbouncer-image 参数需要指定镜像名称"
                fi
                ;;
            --redis-image)
                if [ -n "$2" ] && [ "${2:0:1}" != "-" ]; then
                    REGISTRY_REDIS_IMAGE="$2"
                    shift 2
                else
                    show_error "--redis-image 参数需要指定镜像名称"
                fi
                ;;
            --nginx-image)
                if [ -n "$2" ] && [ "${2:0:1}" != "-" ]; then
                    REGISTRY_NGINX_IMAGE="$2"
                    shift 2
                else
                    show_error "--nginx-image 参数需要指定镜像名称"
                fi
                ;;
            --secsnow-image)
                if [ -n "$2" ] && [ "${2:0:1}" != "-" ]; then
                    REGISTRY_SECSNOW_IMAGE="$2"
                    shift 2
                else
                    show_error "--secsnow-image 参数需要指定镜像名称"
                fi
                ;;
            yes|no)
                CREATE_ADMIN="$1"
                shift
                ;;
            *)
                show_warning "未知参数: $1"
                shift
                ;;
        esac
    done
    
    if [ -z "$CREATE_ADMIN" ]; then
        CREATE_ADMIN="yes"
    fi
    
    export USE_REGISTRY
    export REGISTRY_POSTGRES_IMAGE
    export REGISTRY_PGBOUNCER_IMAGE
    export REGISTRY_REDIS_IMAGE
    export REGISTRY_NGINX_IMAGE
    export REGISTRY_SECSNOW_IMAGE
    
    echo ""
    echo "========================================="
    echo -e "${GREEN}SECSNOW首次安装脚本${NC}"
    echo "========================================="
    echo ""
    
    check_existing_installation
    
    echo -e "${BLUE}安装配置:${NC}"
    echo "  安装目录: ${INSTALL_DIR}"
    if [ "$USE_REGISTRY" = true ]; then
        echo "  安装模式: 从 Docker 仓库拉取镜像"
    else
        echo "  安装模式: 从本地 tar 文件加载镜像"
        echo "  镜像目录: ${BASE_DIR}"
    fi
    echo "  创建管理员: ${CREATE_ADMIN}"
    echo ""
    
    read -p "是否继续安装? (y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        show_warning "安装已取消"
        exit 0
    fi
    
    echo ""
    
    if [ -f "${INSTALL_DIR}/.env" ]; then
        show_warning "检测到已存在的 .env 配置文件"
        BACKUP_FILE=".env.backup.$(date +%Y%m%d_%H%M%S)"
        mv "${INSTALL_DIR}/.env" "${INSTALL_DIR}/${BACKUP_FILE}"
        show_info "已备份为: ${BACKUP_FILE}"
        show_success "旧配置文件已清理，将重新生成"
        echo ""
    fi
    
    check_docker
    check_required_ports
    
    if [ "$USE_REGISTRY" = true ]; then
        pull_images_from_registry
    else
        check_images
        load_images
    fi
    
    generate_env
    optimize_redis_system
    start_services
    run_migrations
    create_admin_user
    
    echo "安装时间: $(date '+%Y-%m-%d %H:%M:%S')" > "${INSTALL_DIR}/.installed"
    echo "安装模式: $([ "$USE_REGISTRY" = true ] && echo '仓库拉取' || echo '本地加载')" >> "${INSTALL_DIR}/.installed"
    
    echo ""
    show_completion
}

# 执行主函数
main "$@"

