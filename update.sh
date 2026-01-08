#!/bin/bash

# SecSnow网络安全综合学习平台 更新脚本 (优化版)
# 用途：更新 SecSnow Web 服务到新版本
# 支持：本地 tar 文件 / Docker Registry 拉取

# 设置颜色输出
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 配置变量
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${SCRIPT_DIR}"
BASE_DIR="${INSTALL_DIR}/base"
BACKUP_DIR="${INSTALL_DIR}/backups"

# 版本信息
VERSION="2.0.0"
UPDATE_DATE=$(date '+%Y-%m-%d %H:%M:%S')

# 更新模式
UPDATE_MODE=""           # 更新模式: "local" 或 "registry"
REGISTRY_IMAGE=""        # 完整镜像名称（包含tag），如：harbor.com/secsnow:v1.0.0

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

# 获取可用的 Docker Compose 命令
get_compose_command() {
    if docker compose version &> /dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose &> /dev/null; then
        echo "docker-compose"
    else
        echo ""
    fi
}

# 读取安装配置信息
read_installation_info() {
    show_step "读取安装配置信息..."
    
    # 检查安装标志文件
    if [ ! -f "${INSTALL_DIR}/.installed" ]; then
        show_warning "未找到安装标志文件 (.installed)，将使用默认配置"
        export INSTALLED_PERFORMANCE_MODE="default"
        export INSTALLED_STORAGE_ENABLED="False"
        return
    fi
    
    # 读取安装信息
    INSTALL_TIME=$(grep "^安装时间:" "${INSTALL_DIR}/.installed" | cut -d':' -f2- | xargs 2>/dev/null || echo "未知")
    INSTALL_MODE=$(grep "^安装模式:" "${INSTALL_DIR}/.installed" | cut -d':' -f2 | xargs 2>/dev/null || echo "未知")
    INSTALLED_PERFORMANCE_MODE=$(grep "^性能模式:" "${INSTALL_DIR}/.installed" | cut -d':' -f2 | xargs 2>/dev/null || echo "default")
    INSTALLED_STORAGE_ENABLED=$(grep "^对象存储:" "${INSTALL_DIR}/.installed" | cut -d':' -f2 | xargs 2>/dev/null || echo "False")
    
    # 显示当前配置
    echo ""
    echo -e "${BLUE}当前安装配置：${NC}"
    echo "  安装时间: ${INSTALL_TIME}"
    echo "  安装方式: ${INSTALL_MODE}"
    echo "  性能模式: ${INSTALLED_PERFORMANCE_MODE}"
    echo "  对象存储: ${INSTALLED_STORAGE_ENABLED}"
    echo ""
    
    # 导出变量供后续使用
    export INSTALLED_PERFORMANCE_MODE
    export INSTALLED_STORAGE_ENABLED
}

# 检查环境
check_environment() {
    show_step "检查更新环境..."
    
    # 检查是否在正确的目录
    if [ ! -f "${INSTALL_DIR}/docker-compose.yml" ]; then
        show_error "未找到 docker-compose.yml，请确保在正确的安装目录运行此脚本"
    fi
    
    # 检查 .env 文件
    if [ ! -f "${INSTALL_DIR}/.env" ]; then
        show_error "未找到 .env 配置文件，请先运行安装脚本"
    fi
    
    # 检查 Docker
    if ! command -v docker &> /dev/null; then
        show_error "Docker 未安装"
    fi
    
    # 检查 Docker 服务
    if ! docker info &> /dev/null 2>&1; then
        show_error "Docker 服务未运行，请先启动 Docker"
    fi
    
    # 检查 Docker Compose
    COMPOSE_CMD=$(get_compose_command)
    if [ -z "$COMPOSE_CMD" ]; then
        show_error "未找到 Docker Compose"
    fi
    
    show_success "环境检查通过"
    show_info "使用 Docker Compose 命令: $COMPOSE_CMD"
}

# 自动选择更新模式
auto_select_update_mode() {
    show_step "确定更新方式..."
    
    # 如果指定了镜像参数，使用 registry 模式
    if [ -n "$REGISTRY_IMAGE" ]; then
        UPDATE_MODE="registry"
        show_success "更新模式: 从 Docker Registry 拉取"
        show_info "目标镜像: $REGISTRY_IMAGE"
        return
    fi
    
    # 否则使用 local 模式
    UPDATE_MODE="local"
    show_success "更新模式: 从本地 tar 文件加载"
}

# 检查本地镜像文件
check_local_image() {
    show_step "检查本地镜像文件..."
    
    if [ ! -d "${BASE_DIR}" ]; then
        show_error "base 目录不存在: ${BASE_DIR}"
    fi
    
    cd "${BASE_DIR}" || show_error "无法进入 base 目录"
    
    # 查找新的 SecSnow 镜像文件
    NEW_IMAGE_FILE=""
    
    # 优先查找带版本号的镜像
    for file in secsnow*.tar; do
        if [ -f "$file" ]; then
            NEW_IMAGE_FILE="$file"
            break
        fi
    done
    
    if [ -z "$NEW_IMAGE_FILE" ]; then
        show_error "未在 ${BASE_DIR} 目录找到新的 SecSnow 镜像文件 (secsnow*.tar)"
    fi
    
    show_success "找到镜像文件: $NEW_IMAGE_FILE"
    ls -lh "$NEW_IMAGE_FILE"
    
    export NEW_IMAGE_FILE
}

# 准备仓库镜像
prepare_registry_image() {
    show_step "准备 Docker Registry 镜像..."
    
    # 使用指定的镜像名称
    NEW_IMAGE_NAME="$REGISTRY_IMAGE"
    
    show_success "目标镜像: $NEW_IMAGE_NAME"
    
    export NEW_IMAGE_NAME
}

# 备份当前数据
backup_data() {
    show_step "备份当前数据..."
    
    # 创建备份目录
    BACKUP_TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
    CURRENT_BACKUP_DIR="${BACKUP_DIR}/${BACKUP_TIMESTAMP}"
    mkdir -p "${CURRENT_BACKUP_DIR}"
    
    cd "${INSTALL_DIR}" || show_error "无法进入安装目录"
    
    # 备份 .env 文件
    show_info "备份配置文件..."
    cp .env "${CURRENT_BACKUP_DIR}/.env.backup" 2>/dev/null || true
    cp .credentials "${CURRENT_BACKUP_DIR}/.credentials.backup" 2>/dev/null || true
    
    # 备份数据库（可选）
    show_info "备份数据库..."
    if docker ps | grep -q secsnow-postgres; then
        docker exec secsnow-postgres pg_dump -U secsnow secsnow > "${CURRENT_BACKUP_DIR}/database.sql" 2>/dev/null
        if [ $? -eq 0 ]; then
            show_success "数据库备份完成: ${CURRENT_BACKUP_DIR}/database.sql"
        else
            show_warning "数据库备份失败，继续更新..."
        fi
    else
        show_warning "PostgreSQL 容器未运行，跳过数据库备份"
    fi
    
    # 记录备份信息
    cat > "${CURRENT_BACKUP_DIR}/backup_info.txt" << EOF
备份时间: ${UPDATE_DATE}
备份目录: ${CURRENT_BACKUP_DIR}
更新前镜像: $(grep SECSNOW_IMAGE .env 2>/dev/null || echo "未知")
EOF
    
    show_success "备份完成: ${CURRENT_BACKUP_DIR}"
    export CURRENT_BACKUP_DIR
}

# 停止服务
stop_services() {
    show_step "停止当前服务..."
    
    cd "${INSTALL_DIR}" || show_error "无法进入安装目录"
    
    COMPOSE_CMD=$(get_compose_command)
    
    # 根据当前安装的性能模式停止服务
    PROFILE_PARAMS=""
    
    # 添加性能模式参数
    if [ "${INSTALLED_PERFORMANCE_MODE}" = "high-performance" ]; then
        PROFILE_PARAMS="--profile high-performance"
        show_info "检测到高性能模式，使用对应参数停止服务"
    fi
    
    # 添加对象存储参数
    if [ "${INSTALLED_STORAGE_ENABLED}" = "True" ] || docker ps | grep -q secsnow-rustfs; then
        PROFILE_PARAMS="${PROFILE_PARAMS} --profile storage"
        show_info "检测到对象存储已启用"
    fi
    
    # 停止服务
    if [ -n "$PROFILE_PARAMS" ]; then
        show_info "停止服务（使用参数: ${PROFILE_PARAMS}）..."
        $COMPOSE_CMD $PROFILE_PARAMS stop web celery-worker celery-worker-container celery-worker-general celery-beat 2>/dev/null || true
    else
        show_info "停止 Web 服务..."
        $COMPOSE_CMD stop web celery-worker celery-beat 2>/dev/null || true
    fi
    
    # 检查是否需要停止 RustFS（如果之前启用了对象存储）
    if docker ps | grep -q secsnow-rustfs; then
        show_info "停止 RustFS 服务..."
        $COMPOSE_CMD --profile storage stop rustfs rustfs-init 2>/dev/null || true
    fi
    
    # 移除旧容器（保留数据卷）
    show_info "移除旧容器..."
    if [ -n "$PROFILE_PARAMS" ]; then
        $COMPOSE_CMD $PROFILE_PARAMS rm -f web celery-worker celery-worker-container celery-worker-general celery-beat 2>/dev/null || true
    else
        $COMPOSE_CMD rm -f web celery-worker celery-beat 2>/dev/null || true
    fi
    
    # 移除 RustFS 容器（如果存在）
    if docker ps -a | grep -q secsnow-rustfs; then
        $COMPOSE_CMD --profile storage rm -f rustfs rustfs-init 2>/dev/null || true
    fi
    
    show_success "服务已停止"
}

# 加载或拉取新镜像
load_new_image() {
    if [ "$UPDATE_MODE" = "local" ]; then
        load_image_from_file
    elif [ "$UPDATE_MODE" = "registry" ]; then
        pull_image_from_registry
    else
        show_error "未知的更新模式: $UPDATE_MODE"
    fi
}

# 从本地文件加载镜像
load_image_from_file() {
    show_step "从本地文件加载镜像..."
    
    cd "${BASE_DIR}" || show_error "无法进入 base 目录"
    
    # 记录旧镜像信息
    OLD_IMAGE=$(docker images | grep secsnow | head -1 | awk '{print $1":"$2}')
    show_info "当前镜像: ${OLD_IMAGE:-无}"
    
    # 加载新镜像
    show_info "加载镜像文件: ${NEW_IMAGE_FILE}..."
    LOAD_OUTPUT=$(docker load -i "${NEW_IMAGE_FILE}" 2>&1)
    
    if [ $? -eq 0 ]; then
        # 提取新镜像名称
        NEW_IMAGE_NAME=$(echo "$LOAD_OUTPUT" | grep -oP 'Loaded image: \K.*' || echo "")
        if [ -z "$NEW_IMAGE_NAME" ]; then
            NEW_IMAGE_NAME=$(echo "$LOAD_OUTPUT" | grep -oP 'Loaded image ID: \K.*' || echo "secsnow:latest")
        fi
        show_success "镜像加载成功: $NEW_IMAGE_NAME"
    else
        show_error "镜像加载失败: $LOAD_OUTPUT"
    fi
    
    # 显示镜像列表
    show_info "当前 SecSnow 镜像列表："
    docker images | grep -E "secsnow|REPOSITORY" || true
    
    export NEW_IMAGE_NAME
}

# 从 Docker Registry 拉取镜像
pull_image_from_registry() {
    show_step "从 Docker Registry 拉取镜像..."
    
    # 记录旧镜像信息
    OLD_IMAGE=$(docker images | grep secsnow | head -1 | awk '{print $1":"$2}')
    show_info "当前镜像: ${OLD_IMAGE:-无}"
    
    # 提取仓库地址
    local registry_host=""
    if [[ "$NEW_IMAGE_NAME" =~ ^([^/]+\.[^/]+)/ ]]; then
        registry_host="${BASH_REMATCH[1]}"
    fi
    
    # 拉取新镜像
    show_info "拉取镜像: ${NEW_IMAGE_NAME}..."
    echo ""
    
    # 第一次尝试：直接拉取（不登录）
    if docker pull "${NEW_IMAGE_NAME}" 2>&1; then
        show_success "镜像拉取成功: $NEW_IMAGE_NAME"
    else
        # 拉取失败，提示登录
        show_warning "镜像拉取失败"
        
        if [ -n "$registry_host" ]; then
            echo ""
            show_info "可能需要登录到镜像仓库"
            read -p "是否尝试登录到 $registry_host 后重试? (y/n): " -n 1 -r
            echo
            
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                show_info "登录到镜像仓库..."
                docker login "$registry_host"
                
                if [ $? -ne 0 ]; then
                    show_error "登录失败，无法继续"
                fi
                
                show_success "登录成功，重新拉取镜像..."
                echo ""
                
                # 第二次尝试：登录后拉取
                if docker pull "${NEW_IMAGE_NAME}"; then
                    show_success "镜像拉取成功: $NEW_IMAGE_NAME"
                else
                    show_error "镜像拉取失败，请检查镜像名称和权限"
                fi
            else
                show_error "未登录，无法拉取私有镜像"
            fi
        else
            show_error "镜像拉取失败，请检查网络连接和镜像名称"
        fi
    fi
    
    # 显示镜像信息
    show_info "镜像详细信息："
    # 提取镜像名称（不含tag）进行过滤
    local image_name_only=$(echo "$NEW_IMAGE_NAME" | cut -d':' -f1 | rev | cut -d'/' -f1 | rev)
    docker images | grep -E "${image_name_only}|REPOSITORY" || true
    
    export NEW_IMAGE_NAME
}

# 更新配置文件
update_config() {
    show_step "更新配置文件..."
    
    cd "${INSTALL_DIR}" || show_error "无法进入安装目录"
    
    if [ -n "$NEW_IMAGE_NAME" ]; then
        # 从镜像名称中提取版本号（tag）
        NEW_VERSION=$(echo "${NEW_IMAGE_NAME}" | grep -oP ':[^:]+$' | sed 's/^://' || echo "unknown")
        if [ -z "$NEW_VERSION" ] || [ "$NEW_VERSION" = "unknown" ]; then
            NEW_VERSION="latest"
        fi
        
        # 备份当前配置
        cp .env .env.pre_update
        
        # 更新镜像配置
        if grep -q "SECSNOW_IMAGE=" .env; then
            sed -i "s|^SECSNOW_IMAGE=.*|SECSNOW_IMAGE=${NEW_IMAGE_NAME}|" .env
            show_success "已更新 SECSNOW_IMAGE 为: ${NEW_IMAGE_NAME}"
        else
            # 如果没有该配置项，添加它
            echo "" >> .env
            echo "# 更新于 ${UPDATE_DATE}" >> .env
            echo "SECSNOW_IMAGE=${NEW_IMAGE_NAME}" >> .env
            show_info "已添加 SECSNOW_IMAGE 配置"
        fi
        
        # 更新版本号
        if grep -q "^SECSNOW_VERSION=" .env; then
            # 已存在版本号配置，更新它
            sed -i "s|^SECSNOW_VERSION=.*|SECSNOW_VERSION=${NEW_VERSION}|" .env
            show_success "已更新 SECSNOW_VERSION 为: ${NEW_VERSION}"
        else
            # 如果没有该配置项，添加它
            show_info "检测到 .env 中没有 SECSNOW_VERSION 配置，正在添加..."
            
            # 尝试方法 1: 在 SECSNOW_IMAGE 行之后添加
            if grep -q "^SECSNOW_IMAGE=" .env; then
                sed -i "/^SECSNOW_IMAGE=/a SECSNOW_VERSION=${NEW_VERSION}" .env
                if [ $? -eq 0 ]; then
                    show_success "已添加 SECSNOW_VERSION 配置（在 SECSNOW_IMAGE 之后）"
                fi
            # 尝试方法 2: 在镜像配置注释后添加
            elif grep -q "^# 🐳 Docker 镜像版本配置" .env; then
                sed -i "/^# 🐳 Docker 镜像版本配置/a # SecSnow 平台版本（从镜像 tag 提取）\nSECSNOW_VERSION=${NEW_VERSION}" .env
                if [ $? -eq 0 ]; then
                    show_success "已添加 SECSNOW_VERSION 配置（在镜像配置区域）"
                fi
            # 尝试方法 3: 在文件末尾添加
            else
                echo "" >> .env
                echo "# SecSnow 平台版本（从镜像 tag 提取，更新脚本添加）" >> .env
                echo "SECSNOW_VERSION=${NEW_VERSION}" >> .env
                show_success "已添加 SECSNOW_VERSION 配置（在文件末尾）"
            fi
        fi
    fi
    
    show_success "配置文件更新完成"
}

# 启动服务
start_services() {
    show_step "启动更新后的服务..."
    
    cd "${INSTALL_DIR}" || show_error "无法进入安装目录"
    
    # 确保必要的数据目录存在
    show_info "检查数据目录..."
    mkdir -p db/postgres 2>/dev/null || true
    mkdir -p redis/data 2>/dev/null || true
    mkdir -p web/media 2>/dev/null || true
    mkdir -p web/static 2>/dev/null || true
    mkdir -p web/log 2>/dev/null || true
    mkdir -p web/log/nginx 2>/dev/null || true
    mkdir -p web/whoosh_index 2>/dev/null || true
    mkdir -p nginx/ssl 2>/dev/null || true
    
    # 检查是否启用对象存储
    STORAGE_ENABLED=$(grep "^SNOW_USE_OBJECT_STORAGE=" .env | cut -d'=' -f2 2>/dev/null || echo "False")
    
    if [ "$STORAGE_ENABLED" = "True" ]; then
        # 创建对象存储相关目录
        show_info "对象存储已启用，创建 RustFS 数据目录..."
        mkdir -p rustfs/data 2>/dev/null || true
        mkdir -p rustfs/logs 2>/dev/null || true
        chmod -R 755 rustfs 2>/dev/null || true
    else
        show_info "对象存储未启用，将跳过 RustFS 服务"
    fi
    
    COMPOSE_CMD=$(get_compose_command)
    
    # 根据安装配置决定启动参数
    PROFILE_PARAMS=""
    
    # 添加性能模式参数
    if [ "${INSTALLED_PERFORMANCE_MODE}" = "high-performance" ]; then
        PROFILE_PARAMS="--profile high-performance"
        show_info "使用高性能模式启动"
    else
        show_info "使用默认模式启动"
    fi
    
    # 添加对象存储参数
    if [ "$STORAGE_ENABLED" = "True" ]; then
        PROFILE_PARAMS="${PROFILE_PARAMS} --profile storage"
        show_info "将启动 RustFS 对象存储服务"
    fi
    
    # 启动服务
    if [ -n "$PROFILE_PARAMS" ]; then
        show_info "启动服务（参数: ${PROFILE_PARAMS}）..."
        if $COMPOSE_CMD $PROFILE_PARAMS up -d; then
            show_success "服务启动成功"
        else
            show_error "服务启动失败，请检查日志"
        fi
    else
        show_info "启动核心服务..."
        if $COMPOSE_CMD up -d; then
            show_success "服务启动成功"
        else
            show_error "服务启动失败，请检查日志"
        fi
    fi
    
    # 等待服务就绪
    show_info "等待服务完全启动..."
    sleep 10
    
    # 显示服务状态
    show_info "服务状态："
    $COMPOSE_CMD ps
}

# 验证 RustFS 密码配置
verify_rustfs_password() {
    show_step "验证 RustFS 密码配置..."
    
    # 检查是否启用了对象存储
    if ! grep -q "^SNOW_USE_OBJECT_STORAGE=True" .env 2>/dev/null; then
        show_info "内置对象存储服务未启用，跳过密码验证"
        return 0
    fi
    
    # 检查 RustFS 是否在运行
    if ! docker ps | grep -q secsnow-rustfs; then
        show_info "RustFS 服务未运行，跳过密码验证"
        return 0
    fi
    
    # 等待 RustFS 完全启动
    show_info "等待 RustFS 服务就绪..."
    sleep 5
    
    # 从 .env 读取密码
    RUSTFS_USER=$(grep "^RUSTFS_ROOT_USER=" .env | cut -d'=' -f2 2>/dev/null || echo "rustfsadmin")
    RUSTFS_PASS=$(grep "^RUSTFS_ROOT_PASSWORD=" .env | cut -d'=' -f2 2>/dev/null || echo "")
    
    if [ -z "$RUSTFS_PASS" ]; then
        show_warning "无法从 .env 读取 RustFS 密码"
        return 1
    fi
    
    # 尝试使用密码连接 RustFS
    show_info "验证 RustFS 密码是否正确..."
    VERIFY_RESULT=$(docker run --rm \
        --network=secsnow-network \
        -e MC_USER="$RUSTFS_USER" \
        -e MC_PASS="$RUSTFS_PASS" \
        --entrypoint /bin/sh \
        minio/mc:latest -c \
        'mc alias set secsnow http://rustfs:9000 "$MC_USER" "$MC_PASS" 2>&1' 2>&1)
    
    if [ $? -eq 0 ]; then
        show_success "✓ RustFS 密码验证成功"
        return 0
    else
        show_error "✗ RustFS 密码验证失败"
        echo "错误信息: $VERIFY_RESULT"
        echo ""
        echo -e "${YELLOW}可能的原因：${NC}"
        echo "  1. RustFS 服务尚未完全启动，请等待几分钟后重试"
        echo "  2. .env 中的密码与 RustFS 实际使用的密码不匹配"
        echo "  3. 需要重置 RustFS 容器和数据目录"
        echo ""
        echo -e "${BLUE}解决方案：${NC}"
        echo "  1. 查看 RustFS 日志: docker logs secsnow-rustfs"
        echo "  2. 重置 RustFS:"
        COMPOSE_CMD=$(get_compose_command)
        echo "     cd ${INSTALL_DIR}"
        echo "     $COMPOSE_CMD --profile storage stop rustfs rustfs-init"
        echo "     $COMPOSE_CMD --profile storage rm -f rustfs rustfs-init"
        echo "     rm -rf rustfs/data"
        echo "     $COMPOSE_CMD --profile storage up -d"
        return 1
    fi
}

# 检查存储配置选择记录
check_storage_config_record() {
    # 检查是否有配置记录文件
    if [ -f "${INSTALL_DIR}/.storage_config" ]; then
        # 读取配置
        source "${INSTALL_DIR}/.storage_config"
        
        if [ "$ASKED_USER" = "true" ]; then
            show_info "检测到已有存储配置记录"
            show_info "存储类型: ${STORAGE_TYPE}"
            return 0  # 已询问过用户
        fi
    fi
    
    return 1  # 未询问过用户
}

# 保存存储配置选择
save_storage_config() {
    local storage_type="$1"
    local enabled="$2"
    
    cat > "${INSTALL_DIR}/.storage_config" << EOF
# 对象存储配置选择
# 由更新脚本自动生成
STORAGE_TYPE=${storage_type}
ENABLE_OBJECT_STORAGE=${enabled}
CONFIG_DATE=$(date '+%Y-%m-%d %H:%M:%S')
ASKED_USER=true
EOF
    
    show_success "存储配置选择已保存"
}

# 检查并初始化对象存储（老用户适配）
check_and_init_object_storage() {
    show_step "检查对象存储配置..."
    
    # 检查是否有旧的 MinIO 配置
    if grep -q "SNOW_USE_MINIO=" .env 2>/dev/null; then
        show_info "检测到旧的 MinIO 配置，迁移到新配置..."
        
        # 读取旧配置
        OLD_MINIO_ENABLED=$(grep "^SNOW_USE_MINIO=" .env | cut -d'=' -f2)
        
        # 迁移配置
        if ! grep -q "SNOW_USE_OBJECT_STORAGE=" .env; then
            # 将 MinIO 配置迁移为通用对象存储配置
            sed -i "s/^SNOW_USE_MINIO=/SNOW_USE_OBJECT_STORAGE=/" .env
            show_success "已迁移为通用对象存储配置"
        fi
        
        # 保存配置记录
        if [ "$OLD_MINIO_ENABLED" = "True" ]; then
            save_storage_config "rustfs" "True"
        else
            save_storage_config "local" "False"
        fi
    fi
    
    # 检查 .env 中是否有对象存储配置
    if ! grep -q "SNOW_USE_OBJECT_STORAGE=" .env 2>/dev/null; then
        show_info "检测到旧版本配置，添加对象存储配置..."
        
        echo ""
        echo "========================================="
        echo -e "${CYAN}📦 对象存储升级${NC}"
        echo "========================================="
        echo ""
        echo -e "${BLUE}新版本必须使用对象存储（RustFS）${NC}"
        echo ""
        echo -e "${GREEN}RustFS 对象存储优势：${NC}"
        echo "  • 高性能：专为对象存储优化"
        echo "  • 可扩展：支持大规模文件存储"
        echo "  • 高可用：支持分布式部署"
        echo "  • 兼容性：兼容 S3 API"
        echo ""
        show_success "正在启用 RustFS 对象存储..."
        
        # 默认启用对象存储
        USE_STORAGE="True"
        save_storage_config "rustfs" "True"
        
        # 生成随机密码（仅使用字母和数字）
        RUSTFS_PASSWORD=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 20 2>/dev/null || echo "rustfsadmin123")
        
        # 添加对象存储配置到 .env
        # 注意：此函数在容器启动前执行，配置添加后容器启动时会直接使用正确的密码
        cat >> .env << EOF

# ================================================
# 📦 系统内置对象存储服务配置
# ================================================
SNOW_USE_OBJECT_STORAGE=${USE_STORAGE}

# RustFS 容器配置
#系统内置对象存储服务rustfs，如果需要使用其他对象存储服务，或者挂载到其他节点请停止内置对象存储服务并配置其他对象存储服务
RUSTFS_ROOT_USER=rustfsadmin
RUSTFS_ROOT_PASSWORD=${RUSTFS_PASSWORD}
RUSTFS_BUCKET_NAME=secsnow
RUSTFS_DATA_DIR=./rustfs/data
RUSTFS_LOG_DIR=./rustfs/logs
RUSTFS_API_PORT=7900
RUSTFS_CONSOLE_PORT=7901
# CORS 设置，控制台与 S3 API 都放开来源
RUSTFS_CONSOLE_CORS_ALLOWED_ORIGINS=*
RUSTFS_CORS_ALLOWED_ORIGINS=*

# RustFS 镜像配置
RUSTFS_IMAGE=rustfs/rustfs:latest
MINIO_MC_IMAGE=minio/mc:latest

# ================================================
# 📦对象存储配置节点配置
# ================================================
# 使用这些变量连接到 RustFS，这里支持其他节点挂载请
# 存储访问凭证
SNOW_STORAGE_ACCESS_KEY=rustfsadmin
# 存储访问密钥
SNOW_STORAGE_SECRET_KEY=${RUSTFS_PASSWORD}

# 存储桶名称，如果您使用用本地存储节点，需要去nginx配置文件中添加桶名称代理的配置，因为本地存储节点不会暴露桶名称做了层代理。
#默认桶的名称为secsnow，如果您换桶名，也需要将默认的存储文件上传至新桶。

SNOW_STORAGE_BUCKET_NAME=secsnow

# 存储节点地址，本地内部节点地址为 http://rustfs:9000
SNOW_STORAGE_ENDPOINT_URL=http://rustfs:9000
# 区域
SNOW_STORAGE_REGION=us-east-1
# 文件路径前缀
# 如果设置为 'media'，文件会存储在 s3://secsnow/media/uploads/file.jpg
# 当前留空，文件存储在 s3://secsnow/uploads/file.jpg
SNOW_STORAGE_LOCATION=

# SSL 配置
SNOW_STORAGE_USE_SSL=False
SNOW_STORAGE_VERIFY_SSL=False

# 公开访问配置
SNOW_STORAGE_PUBLIC_URL=
EOF
        show_success "对象存储配置已添加到 .env 文件"
        show_info "生成的 RustFS 密码: ${RUSTFS_PASSWORD}"
        show_info "容器启动时将自动使用此密码初始化 RustFS"
        
        show_info "对象存储已启用，将在服务重启后生效"
        
        # 拉取 RustFS 镜像
        show_info "拉取 RustFS 相关镜像..."
        docker pull rustfs/rustfs:latest 2>/dev/null || show_warning "RustFS 镜像拉取失败，将在启动时自动拉取"
        docker pull minio/mc:latest 2>/dev/null || show_warning "MinIO Client 镜像拉取失败"
        
        # 提示需要重启服务
        echo ""
        echo -e "${YELLOW}重要提示：${NC}"
        echo "  对象存储配置已添加，需要重启服务以启动 RustFS"
        echo "  服务将在更新流程中自动重启"
        echo ""
        
        # 检测本地文件（仅提示，不询问）
        if [ -d "web/media" ] && [ "$(find web/media -type f 2>/dev/null | wc -l)" -gt 0 ]; then
            LOCAL_FILES=$(find web/media -type f 2>/dev/null | wc -l)
            echo ""
            show_info "检测到本地文件: $LOCAL_FILES 个文件在 web/media 目录"
            if [ "$AUTO_MIGRATE_MEDIA" = true ]; then
                show_success "已设置自动迁移参数，将在更新完成后迁移文件"
            else
                show_info "如需迁移文件到对象存储，请使用参数: --migrate-media"
            fi
            echo ""
        fi
    else
        # 已有配置，检查状态
        STORAGE_ENABLED=$(grep "^SNOW_USE_OBJECT_STORAGE=" .env | cut -d'=' -f2)
        
        if [ "$STORAGE_ENABLED" = "True" ]; then
            show_success "对象存储已启用"
            
            # 检查 RustFS 服务是否在运行
            if docker ps | grep -q secsnow-rustfs; then
                show_success "RustFS 服务运行正常"
            else
                show_warning "RustFS 服务未运行，将在启动服务时自动启动"
            fi
        else
            # 如果配置为 False，说明用户禁用了对象存储，跳过相关操作
            show_info "对象存储未启用（SNOW_USE_OBJECT_STORAGE=False）"
            show_info "如需启用对象存储，请修改 .env 文件中的 SNOW_USE_OBJECT_STORAGE=True"
            show_info "跳过对象存储相关操作"
        fi
    fi
}

# 检查是否需要迁移 media 文件到对象存储
check_media_migration() {
    show_info "检查 media 文件迁移状态..."
    
    # 检查对象存储中是否已有文件
    STORAGE_USER=$(grep "^RUSTFS_ROOT_USER=" .env | cut -d'=' -f2)
    STORAGE_PASSWORD=$(grep "^RUSTFS_ROOT_PASSWORD=" .env | cut -d'=' -f2)
    STORAGE_BUCKET=$(grep "^RUSTFS_BUCKET_NAME=" .env | cut -d'=' -f2)
    
    # 检查对象存储中的文件数量
    STORAGE_FILE_COUNT=$(docker run --rm \
        --network=secsnow-network \
        -e MC_USER="$STORAGE_USER" \
        -e MC_PASS="$STORAGE_PASSWORD" \
        -e BUCKET="$STORAGE_BUCKET" \
        --entrypoint /bin/sh \
        minio/mc:latest -c '
            mc alias set secsnow http://rustfs:9000 "$MC_USER" "$MC_PASS" >/dev/null 2>&1
            mc ls --recursive secsnow/"$BUCKET"/ 2>/dev/null | wc -l
        ' 2>/dev/null || echo "0")
    
    LOCAL_FILE_COUNT=$(find web/media -type f 2>/dev/null | wc -l)
    
    echo ""
    echo -e "${YELLOW}文件迁移状态:${NC}"
    echo "  本地文件数: $LOCAL_FILE_COUNT"
    echo "  对象存储文件数: $STORAGE_FILE_COUNT"
    
    # 如果对象存储中文件明显少于本地，提示需要迁移
    if [ "$STORAGE_FILE_COUNT" -lt "$((LOCAL_FILE_COUNT / 2))" ] && [ "$LOCAL_FILE_COUNT" -gt 0 ]; then
        show_warning "对象存储中文件数量较少，可能需要迁移"
        show_info "如需迁移文件，请使用参数: --migrate-media"
    else
        show_success "文件已同步到对象存储"
    fi
}

# 启用对象存储
enable_object_storage() {
    show_step "启用 RustFS 对象存储..."
    
    # 修改 .env 配置
    sed -i.bak 's/^SNOW_USE_OBJECT_STORAGE=.*/SNOW_USE_OBJECT_STORAGE=True/' .env
    
    # 生成随机密码（如果是默认密码）
    CURRENT_PASSWORD=$(grep "^RUSTFS_ROOT_PASSWORD=" .env | cut -d'=' -f2 2>/dev/null || echo "rustfsadmin")
    PASSWORD_CHANGED=false
    NEW_PASSWORD=""
    
    if [ "$CURRENT_PASSWORD" = "rustfsadmin" ]; then
        NEW_PASSWORD=$(openssl rand -base64 32 | tr -dc 'A-Za-z0-9' | head -c 20 2>/dev/null || echo "rustfsadmin123")
        sed -i "s/^RUSTFS_ROOT_PASSWORD=.*/RUSTFS_ROOT_PASSWORD=${NEW_PASSWORD}/" .env
        # 同时更新 Django 应用层密码
        if grep -q "^SNOW_STORAGE_SECRET_KEY=" .env; then
            sed -i "s/^SNOW_STORAGE_SECRET_KEY=.*/SNOW_STORAGE_SECRET_KEY=${NEW_PASSWORD}/" .env
        fi
        show_success "已生成随机 RustFS 密码: ${NEW_PASSWORD}"
        PASSWORD_CHANGED=true
    else
        show_info "使用现有 RustFS 密码"
    fi
    
    # 如果密码变更了，需要重置 RustFS 容器
    if [ "$PASSWORD_CHANGED" = true ] && docker ps -a | grep -q secsnow-rustfs; then
        show_warning "密码已更新，需要重置 RustFS 容器以应用新密码"
        
        # 停止并删除容器
        COMPOSE_CMD=$(get_compose_command)
        $COMPOSE_CMD --profile storage stop rustfs rustfs-init 2>/dev/null || true
        $COMPOSE_CMD --profile storage rm -f rustfs rustfs-init 2>/dev/null || true
        
        # 删除数据目录（强制重新初始化）
        if [ -d "rustfs/data" ]; then
            show_info "删除 RustFS 旧数据目录以应用新密码..."
            rm -rf rustfs/data
        fi
        
        show_success "RustFS 容器已重置，将使用新密码重新初始化"
    fi
    
    # 创建必要的目录
    mkdir -p "${INSTALL_DIR}/rustfs/data" 2>/dev/null || true
    mkdir -p "${INSTALL_DIR}/rustfs/logs" 2>/dev/null || true
    chmod -R 755 "${INSTALL_DIR}/rustfs" 2>/dev/null || true
    
    # 拉取 RustFS 镜像
    show_info "拉取 RustFS 相关镜像..."
    docker pull rustfs/rustfs:latest 2>/dev/null || show_warning "RustFS 镜像拉取失败，将在启动时自动拉取"
    docker pull minio/mc:latest 2>/dev/null || show_warning "MinIO Client 镜像拉取失败"
    
    show_success "RustFS 对象存储已启用"
    show_info "RustFS 将在服务重启后自动运行"
    
    # 检测本地文件（仅提示，不询问）
    if [ -d "web/media" ] && [ "$(find web/media -type f 2>/dev/null | wc -l)" -gt 0 ]; then
        LOCAL_FILES=$(find web/media -type f 2>/dev/null | wc -l)
        echo ""
        show_info "检测到本地文件: $LOCAL_FILES 个文件在 web/media 目录"
        if [ "$AUTO_MIGRATE_MEDIA" = true ]; then
            show_success "已设置自动迁移参数，将在更新完成后迁移文件"
        else
            show_info "如需迁移文件到对象存储，请使用参数: --migrate-media"
        fi
        echo ""
    fi
}

# 迁移 media 文件到对象存储（增强版 - 从 migrate_to_rustfs.sh 整合）
migrate_media_to_storage() {
    show_step "迁移 media 文件到对象存储..."
    
    echo ""
    echo "========================================="
    echo -e "${CYAN}  RustFS 文件迁移工具${NC}"
    echo "========================================="
    echo ""
    
    # 检查目录
    if [ ! -d "web/media" ]; then
        show_error "web/media 目录不存在"
        return 1
    fi
    
    # 从 .env 读取配置
    STORAGE_USER=$(grep "^RUSTFS_ROOT_USER=" .env | cut -d'=' -f2 2>/dev/null || echo "rustfsadmin")
    STORAGE_PASSWORD=$(grep "^RUSTFS_ROOT_PASSWORD=" .env | cut -d'=' -f2 2>/dev/null || echo "rustfsadmin")
    STORAGE_BUCKET=$(grep "^RUSTFS_BUCKET_NAME=" .env | cut -d'=' -f2 2>/dev/null || echo "secsnow")
    
    show_info "配置信息："
    echo "  用户: $STORAGE_USER"
    echo "  Bucket: $STORAGE_BUCKET"
    echo ""
    
    # 统计本地文件
    LOCAL_FILE_COUNT=$(find web/media -type f 2>/dev/null | wc -l)
    show_info "本地文件数: $LOCAL_FILE_COUNT"
    
    if [ "$LOCAL_FILE_COUNT" -eq 0 ]; then
        show_warning "没有文件需要迁移"
        return 0
    fi
    
    # 确保 RustFS 服务运行
    if ! docker ps | grep -q secsnow-rustfs; then
        show_warning "RustFS 未运行，正在启动 RustFS 服务..."
        
        # 获取 compose 命令
        COMPOSE_CMD=$(get_compose_command)
        $COMPOSE_CMD --profile storage up -d 2>/dev/null || true
        
        show_info "等待 RustFS 启动..."
        sleep 20
        
        # 再次检查
        if ! docker ps | grep -q secsnow-rustfs; then
            show_error "RustFS 启动失败，请检查日志: docker logs secsnow-rustfs"
            return 1
        fi
    fi
    
    show_success "RustFS 运行正常"
    echo ""
    
    show_info "开始迁移 $LOCAL_FILE_COUNT 个文件..."
    echo ""
    
    # 步骤 1/4: 配置 mc 客户端
    show_info "步骤 1/4: 配置 mc 客户端..."
    
    # 先测试网络连接（RustFS 使用 /health 端点）
    if ! docker run --rm \
        --network=secsnow-network \
        alpine/curl:latest -f -s \
        "http://rustfs:9000/health" >/dev/null 2>&1; then
        show_error "无法连接到 RustFS 服务，请确保 RustFS 容器正在运行"
        show_info "提示: 运行 'docker ps | grep rustfs' 检查服务状态"
        return 1
    fi
    
    # 使用环境变量传递密码，避免特殊字符问题
    MC_CONFIG_ERROR=$(docker run --rm \
        --network=secsnow-network \
        -e MC_USER="$STORAGE_USER" \
        -e MC_PASS="$STORAGE_PASSWORD" \
        --entrypoint /bin/sh \
        minio/mc:latest -c \
        'mc alias set secsnow http://rustfs:9000 "$MC_USER" "$MC_PASS"' 2>&1)
    
    if [ $? -ne 0 ]; then
        show_error "mc 客户端配置失败"
        echo "错误详情: $MC_CONFIG_ERROR"
        show_info "请检查:"
        echo "  1. RustFS 服务是否正常运行"
        echo "  2. 用户名和密码是否正确"
        echo "  3. 网络连接是否正常"
        return 1
    fi
    show_success "✓ mc 客户端配置完成"
    
    # 步骤 2/4: 检查/创建 bucket
    show_info "步骤 2/4: 检查/创建 bucket..."
    BUCKET_ERROR=$(docker run --rm \
        --network=secsnow-network \
        -e MC_USER="$STORAGE_USER" \
        -e MC_PASS="$STORAGE_PASSWORD" \
        -e BUCKET="$STORAGE_BUCKET" \
        --entrypoint /bin/sh \
        minio/mc:latest -c \
        'mc alias set secsnow http://rustfs:9000 "$MC_USER" "$MC_PASS" >/dev/null 2>&1 && \
         mc mb secsnow/"$BUCKET" --ignore-existing 2>&1 && \
         mc anonymous set public secsnow/"$BUCKET" 2>&1' 2>&1)
    
    if [ $? -ne 0 ]; then
        show_error "Bucket 创建失败"
        echo "错误详情: $BUCKET_ERROR"
        return 1
    fi
    show_success "✓ Bucket 已就绪"
    
    # 步骤 3/4: 上传文件
    show_info "步骤 3/4: 上传文件（可能需要几分钟）..."
    echo ""
    
    docker run --rm \
        -v "$(pwd)/web/media:/media" \
        --network=secsnow-network \
        -e MC_USER="$STORAGE_USER" \
        -e MC_PASS="$STORAGE_PASSWORD" \
        -e BUCKET="$STORAGE_BUCKET" \
        --entrypoint /bin/sh \
        minio/mc:latest -c \
        'mc alias set secsnow http://rustfs:9000 "$MC_USER" "$MC_PASS" >/dev/null 2>&1 && \
         mc cp --recursive /media/ secsnow/"$BUCKET"/' 2>&1
    
    UPLOAD_STATUS=$?
    echo ""
    
    if [ $UPLOAD_STATUS -ne 0 ]; then
        show_error "文件上传失败"
        show_info "提示: 如果上传部分文件后失败，可以重新运行脚本继续上传"
        return 1
    fi
    show_success "✓ 文件上传完成"
    
    # 步骤 4/4: 验证结果
    show_info "步骤 4/4: 验证结果..."
    STORAGE_FILE_COUNT=$(docker run --rm \
        --network=secsnow-network \
        -e MC_USER="$STORAGE_USER" \
        -e MC_PASS="$STORAGE_PASSWORD" \
        -e BUCKET="$STORAGE_BUCKET" \
        --entrypoint /bin/sh \
        minio/mc:latest -c \
        'mc alias set secsnow http://rustfs:9000 "$MC_USER" "$MC_PASS" >/dev/null 2>&1 && \
         mc ls --recursive secsnow/"$BUCKET"/ 2>/dev/null | wc -l' 2>/dev/null || echo "0")
    
    # 确保是纯数字
    STORAGE_FILE_COUNT=$(echo "$STORAGE_FILE_COUNT" | grep -o '[0-9]*' | tail -1)
    
    echo ""
    echo "========================================="
    echo -e "${CYAN}  迁移结果${NC}"
    echo "========================================="
    echo "  本地文件: $LOCAL_FILE_COUNT"
    echo "  RustFS 文件: $STORAGE_FILE_COUNT"
    echo "========================================="
    echo ""
    
    if [ "$STORAGE_FILE_COUNT" -ge "$LOCAL_FILE_COUNT" ]; then
        show_success "✅ 迁移成功！"
        echo ""
        
        # 自动备份本地文件
        if [ -d "web/media.backup" ]; then
            show_warning "web/media.backup 已存在，将覆盖"
            rm -rf web/media.backup
        fi
        mv web/media web/media.backup
        mkdir -p web/media
        show_success "本地目录已重命名为 media.backup"
        echo ""
        show_info "后续步骤："
        echo "  1. 测试文件访问: http://你的域名/media/文件路径"
        echo "  2. 确认无误后可删除备份: rm -rf web/media.backup"
        echo "  3. 访问 RustFS 控制台: http://你的IP:7901/"
    else
        show_error "❌ 文件数量不匹配"
        echo ""
        echo "排查步骤："
        echo "  1. 查看 RustFS 日志: docker logs secsnow-rustfs"
        echo "  2. 检查网络: docker network inspect secsnow-network"
        echo "  3. 手动验证: docker run --rm --network=secsnow-network alpine/curl curl http://rustfs:9000/health"
        return 1
    fi
    
    echo ""
}

# 清理废弃的简历表
clean_resume_tables() {
    show_step "检查并清理废弃的简历表..."
    
    # 检查容器是否运行
    if ! docker ps | grep -q secsnow-postgres; then
        show_warning "PostgreSQL 容器未运行，跳过简历表清理"
        return
    fi
    
    # 检查简历表是否存在
    RESUME_TABLES=$(docker exec secsnow-postgres psql -U secsnow -d secsnow -t -c "
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' 
        AND table_name IN ('resume_resume', 'resume_resumetemplate')
    " 2>/dev/null | tr -d ' ')
    
    if [ -z "$RESUME_TABLES" ]; then
        show_info "未发现简历相关表，跳过清理"
        return
    fi
    
    # 统计表中的数据
    show_info "发现废弃的简历相关表，准备清理..."
    for table in $RESUME_TABLES; do
        COUNT=$(docker exec secsnow-postgres psql -U secsnow -d secsnow -t -c "SELECT COUNT(*) FROM $table" 2>/dev/null | tr -d ' ')
        show_warning "  - $table: $COUNT 条数据"
    done
    
    echo ""
    show_warning "简历模块已废弃，将自动清理相关数据表"
    
    # 删除表（强制执行）
    show_info "正在删除简历表..."
    for table in $RESUME_TABLES; do
        docker exec secsnow-postgres psql -U secsnow -d secsnow -c "DROP TABLE IF EXISTS $table CASCADE" 2>/dev/null
        if [ $? -eq 0 ]; then
            show_success "  ✓ 已删除: $table"
        else
            show_warning "  ✗ 删除失败: $table"
        fi
    done
    
    # 清理迁移记录
    show_info "清理简历 app 的迁移记录..."
    docker exec secsnow-postgres psql -U secsnow -d secsnow -c "DELETE FROM django_migrations WHERE app = 'resume'" 2>/dev/null
    if [ $? -eq 0 ]; then
        show_success "  ✓ 迁移记录已清理"
    fi
    
    show_success "简历表清理完成"
}

# 执行数据库迁移
run_migrations() {
    show_step "执行数据库迁移..."
    
    # 等待 Web 服务就绪
    show_info "等待 Web 服务就绪..."
    sleep 5
    
    # 检查容器是否运行
    if ! docker ps | grep -q secsnow-web; then
        show_error "Web 容器未运行，无法执行迁移"
    fi
    
    # 清理废弃的简历表（在迁移之前）
    clean_resume_tables
    
    echo ""
    
    # 执行迁移
    show_info "检查并应用数据库迁移..."
    docker exec secsnow-web python manage.py migrate --noinput
    
    if [ $? -eq 0 ]; then
        show_success "数据库迁移完成"
    else
        show_warning "数据库迁移可能有问题，请检查日志"
    fi
    
    # 收集静态文件
    show_info "收集静态文件..."
    docker exec secsnow-web python manage.py collectstatic --noinput 2>/dev/null
    
    if [ $? -eq 0 ]; then
        show_success "静态文件收集完成"
    else
        show_warning "静态文件收集可能有问题"
    fi

    show_info "初始化功能模块..."
    docker exec secsnow-web python manage.py init_license_modules

    if [ $? -eq 0 ]; then
        show_success "功能模块初始化完成"
    else
        show_warning "功能模块初始化可能有问题"
    fi
}

# 验证更新
verify_update() {
    show_step "验证更新结果..."
    
    # 检查 Web 服务健康状态
    show_info "检查服务健康状态..."
    
    # 等待服务完全启动
    sleep 5
    
    # 检查容器状态
    WEB_STATUS=$(docker inspect -f '{{.State.Status}}' secsnow-web 2>/dev/null || echo "未知")
    
    if [ "$WEB_STATUS" == "running" ]; then
        show_success "Web 服务运行正常"
    else
        show_warning "Web 服务状态: $WEB_STATUS"
    fi
    
    # 尝试访问健康检查端点
    show_info "测试服务响应..."
    sleep 3
    
    # 检查是否能访问
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:80 2>/dev/null | grep -qE "200|301|302"; then
        show_success "服务响应正常"
    else
        show_warning "服务可能尚未完全就绪，请稍后手动验证"
    fi
    
    # 显示当前运行的镜像版本
    show_info "当前运行的镜像："
    docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}" | grep -E "secsnow|NAMES"
}

# 清理旧镜像（可选）
cleanup_old_images() {
    show_step "清理旧镜像（可选）..."
    
    # 显示所有 SecSnow 相关镜像
    show_info "当前 SecSnow 镜像列表："
    docker images | grep -E "secsnow|REPOSITORY"
    
    echo ""
    read -p "是否删除旧版本镜像以释放空间? (y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        # 获取当前使用的镜像
        CURRENT_IMAGE=$(docker inspect -f '{{.Config.Image}}' secsnow-web 2>/dev/null || echo "")
        
        # 删除未使用的 SecSnow 镜像
        show_info "清理未使用的镜像..."
        docker images | grep secsnow | grep -v "$CURRENT_IMAGE" | awk '{print $3}' | xargs -r docker rmi 2>/dev/null || true
        
        # 清理悬空镜像
        docker image prune -f 2>/dev/null || true
        
        show_success "旧镜像清理完成"
    else
        show_info "跳过清理旧镜像"
    fi
}

# 显示更新完成信息
show_completion() {
    echo ""
    echo "========================================="
    echo -e "${GREEN}🎉 更新完成！${NC}"
    echo "========================================="
    echo ""
    echo -e "${BLUE}更新信息:${NC}"
    echo "  更新时间: ${UPDATE_DATE}"
    echo "  更新模式: ${UPDATE_MODE}"
    if [ "$UPDATE_MODE" = "local" ]; then
        echo "  镜像文件: ${NEW_IMAGE_FILE:-未知}"
    else
        echo "  镜像来源: Docker Registry"
        echo "  镜像标识: ${REGISTRY_IMAGE:-未知}"
    fi
    echo "  新镜像: ${NEW_IMAGE_NAME:-未知}"
    
    # 显示版本号
    CURRENT_VERSION=$(grep "^SECSNOW_VERSION=" .env | cut -d'=' -f2 2>/dev/null || echo "未知")
    echo "  当前版本: ${CURRENT_VERSION}"
    
    # 显示性能模式
    echo "  性能模式: ${INSTALLED_PERFORMANCE_MODE}"
    
    echo "  备份目录: ${CURRENT_BACKUP_DIR:-未备份}"
    echo ""
    
    COMPOSE_CMD=$(get_compose_command)
    
    # 构建 profile 参数
    PROFILE_PARAMS=""
    if [ "${INSTALLED_PERFORMANCE_MODE}" = "high-performance" ]; then
        PROFILE_PARAMS="--profile high-performance"
    fi
    if [ "$STORAGE_ENABLED" = "True" ]; then
        PROFILE_PARAMS="${PROFILE_PARAMS} --profile storage"
    fi
    
    echo -e "${BLUE}常用命令:${NC}"
    echo "  查看服务状态:"
    if [ -n "$PROFILE_PARAMS" ]; then
        echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD ${PROFILE_PARAMS} ps"
    else
        echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD ps"
    fi
    echo ""
    echo "  查看 Web 日志:"
    echo "    docker logs -f secsnow-web"
    echo ""
    echo "  查看所有服务日志:"
    if [ -n "$PROFILE_PARAMS" ]; then
        echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD ${PROFILE_PARAMS} logs -f"
    else
        echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD logs -f"
    fi
    echo ""
    
    if [ "$UPDATE_MODE" = "local" ]; then
        echo "  下次更新（本地文件）:"
        echo "    1. 将新镜像文件放入: ${BASE_DIR}/"
        echo "    2. 运行更新脚本: bash $0 -y"
    else
        echo "  下次更新（从仓库拉取）:"
        # 提取镜像名称（不含tag）
        local image_base=$(echo "$REGISTRY_IMAGE" | cut -d':' -f1)
        echo "    bash $0 -r ${image_base}:<新版本号> -y"
    fi
    echo ""
    
    # 检查是否启用了对象存储（用于后续显示）
    STORAGE_ENABLED=$(grep "^SNOW_USE_OBJECT_STORAGE=" .env | cut -d'=' -f2 2>/dev/null || echo "False")
    
    echo -e "${BLUE}回滚步骤（如果需要）:${NC}"
    if [ -n "$PROFILE_PARAMS" ]; then
        echo "  1. 停止服务: cd ${INSTALL_DIR} && $COMPOSE_CMD ${PROFILE_PARAMS} down"
    else
        echo "  1. 停止服务: cd ${INSTALL_DIR} && $COMPOSE_CMD down"
    fi
    echo "  2. 恢复配置: cp ${CURRENT_BACKUP_DIR}/.env.backup .env"
    if [ "$UPDATE_MODE" = "registry" ]; then
        local image_base=$(echo "$REGISTRY_IMAGE" | cut -d':' -f1)
        echo "  3. 拉取旧版本: docker pull ${image_base}:<旧版本号>"
        echo "  4. 更新 .env 文件中的 SECSNOW_IMAGE"
    else
        echo "  3. 重新加载旧镜像"
    fi
    if [ -n "$PROFILE_PARAMS" ]; then
        echo "  5. 启动服务: $COMPOSE_CMD ${PROFILE_PARAMS} up -d"
    else
        echo "  5. 启动服务: $COMPOSE_CMD up -d"
    fi
    echo ""
    
    if [ "$STORAGE_ENABLED" = "True" ]; then
        # 显示对象存储信息
        echo -e "${BLUE}对象存储 (RustFS):${NC}"
        echo "  状态: 已启用"
        echo "  控制台: http://服务器IP:7901/"
        
        # 从 .env 读取并显示密码
        RUSTFS_USER=$(grep "^RUSTFS_ROOT_USER=" .env | cut -d'=' -f2 2>/dev/null || echo "rustfsadmin")
        RUSTFS_PASS=$(grep "^RUSTFS_ROOT_PASSWORD=" .env | cut -d'=' -f2 2>/dev/null || echo "未找到")
        echo "  用户名: ${RUSTFS_USER}"
        echo "  密码: ${RUSTFS_PASS}"
        
        echo "  文件访问: http://服务器IP/media/（Nginx 自动代理）"
        echo ""
    else
        # 对象存储未启用
        echo -e "${BLUE}对象存储:${NC}"
        echo "  状态: 未启用"
        echo "  文件存储: 本地文件系统 (web/media)"
        echo ""
    fi
    
    echo -e "${YELLOW}提示:${NC}"
    echo "  1. 如遇问题，可查看日志: docker logs secsnow-web"
    echo "  2. 备份文件保存在: ${BACKUP_DIR}"
    echo "  3. 建议测试主要功能是否正常"
    echo "  4. 数据库数据已保留，无需担心数据丢失"
    
    # 根据对象存储状态显示不同提示
    if [ "$STORAGE_ENABLED" = "True" ]; then
        echo "  5. 对象存储已启用，新上传文件将保存到 RustFS"
        if [ -d "web/media.backup" ]; then
            echo "  6. 旧 media 文件已备份到 web/media.backup"
            echo "  7. 确认无误后可删除备份: rm -rf web/media.backup"
        fi
        # 检查是否还有本地文件未迁移
        if [ -d "web/media" ] && [ "$(find web/media -type f 2>/dev/null | wc -l)" -gt 0 ]; then
            LOCAL_FILES=$(find web/media -type f 2>/dev/null | wc -l)
            if [ "$LOCAL_FILES" -gt 10 ]; then
                echo ""
                echo -e "${YELLOW}⚠️  注意: web/media 中还有 $LOCAL_FILES 个文件未迁移${NC}"
                echo "  建议迁移到 RustFS 以获得更好的性能和可扩展性"
            fi
        fi
    else
        echo "  5. 对象存储未启用，文件将保存到本地 web/media 目录"
        echo "  6. 如需启用对象存储，请修改 .env 中的 SNOW_USE_OBJECT_STORAGE=True"
    fi
    echo ""
    echo "========================================="
    echo -e "${CYAN}访问地址:${NC}"
    
    # 读取 .env 文件获取端口信息
    if [ -f "${INSTALL_DIR}/.env" ]; then
        HTTP_PORT=$(grep "^HTTP_PORT=" "${INSTALL_DIR}/.env" | cut -d'=' -f2 || echo "80")
        HTTPS_PORT=$(grep "^HTTPS_PORT=" "${INSTALL_DIR}/.env" | cut -d'=' -f2 || echo "443")
        
        echo "  HTTP:  http://服务器IP:${HTTP_PORT}"
        if [ -n "$HTTPS_PORT" ]; then
            echo "  HTTPS: https://服务器IP:${HTTPS_PORT}"
        fi
    fi
    
    echo "========================================="
}

# 显示帮助信息
show_help() {
    echo ""
    echo "SecSnow 更新脚本 v${VERSION}"
    echo ""
    echo "用法: $0 [选项]"
    echo ""
    echo "选项:"
    echo "  -h, --help                        显示此帮助信息"
    echo "  -y, --yes                         跳过确认提示，直接更新"
    echo "  -r, --registry-image <完整镜像>   从仓库拉取（格式：仓库/镜像:tag）"
    echo "  --no-backup                       跳过备份步骤"
    echo "  --no-migrate                      跳过数据库迁移"
    echo "  --cleanup                         更新后自动清理旧镜像"
    echo "  --enable-storage                  自动启用对象存储（不询问）"
    echo "  --migrate-media                   自动迁移 media 文件到对象存储（不询问）"
    echo ""
    echo "注意:"
    echo "  • 废弃的简历表会自动清理（无需参数）"
    echo ""
    echo "更新方式:"
    echo ""
    echo "  1. 从本地 tar 文件更新（默认）："
    echo "     $0"
    echo "     $0 -y"
    echo "     (需要将 secsnow*.tar 放入 ${BASE_DIR} 目录)"
    echo ""
    echo "  2. 从 Docker Registry 拉取："
    echo "     $0 -r <完整镜像名称:tag>"
    echo ""
    echo "完整示例:"
    echo ""
    echo "  # 从本地文件更新"
    echo "  $0 -y --cleanup"
    echo ""
    echo "  # 老用户首次启用对象存储（自动迁移本地文件）"
    echo "  $0 -y --enable-storage --migrate-media"
    echo ""
    echo "  # 交互式更新（会询问是否启用对象存储和迁移文件）"
    echo "  $0"
    echo ""
    echo "  # 从 Docker Hub 拉取"
    echo "  $0 -r secsnow/secsnow:v1.0.0"
    echo "  $0 -r secsnow/secsnow:latest -y"
    echo ""
    echo "  # 从私有 Harbor 拉取"
    echo "  $0 -r harbor.company.com/secsnow/secsnow:v1.0.0"
    echo "  $0 -r harbor.company.com/secsnow/secsnow:latest --cleanup"
    echo ""
    echo "  # 从阿里云容器镜像服务拉取"
    echo "  $0 -r crpi-xxx.cn-chengdu.personal.cr.aliyuncs.com/secsnow/secsnow_cty:1.0.1"
    echo ""
    echo "  # 从私有仓库拉取（需要先登录）"
    echo "  docker login registry.example.com:5000"
    echo "  $0 -r registry.example.com:5000/secsnow:stable -y"
    echo ""
    echo "注意事项:"
    echo "  1. 镜像名称必须包含完整的 tag（如 :v1.0.0 或 :latest）"
    echo "  2. 私有仓库需要先使用 docker login 登录"
    echo "  3. 当前服务必须正在运行"
    echo "  4. 确保有足够的磁盘空间用于备份"
    echo ""
}

# 主函数
main() {
    # 解析参数
    SKIP_CONFIRM=false
    SKIP_BACKUP=false
    SKIP_MIGRATE=false
    AUTO_CLEANUP=false
    AUTO_CLEAN_RESUME=false
    AUTO_ENABLE_STORAGE=false
    AUTO_MIGRATE_MEDIA=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            -h|--help)
                show_help
                exit 0
                ;;
            -y|--yes)
                SKIP_CONFIRM=true
                shift
                ;;
            -r|--registry-image)
                REGISTRY_IMAGE="$2"
                if [ -z "$REGISTRY_IMAGE" ]; then
                    show_error "参数 -r/--registry-image 需要指定完整镜像名称（包含tag）"
                fi
                shift 2
                ;;
            --no-backup)
                SKIP_BACKUP=true
                shift
                ;;
            --no-migrate)
                SKIP_MIGRATE=true
                shift
                ;;
            --cleanup)
                AUTO_CLEANUP=true
                shift
                ;;
            --clean-resume)
                # 参数已废弃：简历表现在自动清理
                show_warning "参数 --clean-resume 已废弃，简历表会自动清理"
                shift
                ;;
            --enable-storage)
                AUTO_ENABLE_STORAGE=true
                shift
                ;;
            --migrate-media)
                AUTO_MIGRATE_MEDIA=true
                shift
                ;;
            *)
                show_warning "未知参数: $1"
                show_info "使用 -h 或 --help 查看帮助"
                exit 1
                ;;
        esac
    done
    
    echo ""
    echo "========================================="
    echo -e "${GREEN}SecSnow 服务更新脚本 v${VERSION}${NC}"
    echo "========================================="
    echo ""
    
    # 显示配置信息
    echo -e "${BLUE}更新配置:${NC}"
    echo "  安装目录: ${INSTALL_DIR}"
    echo "  镜像目录: ${BASE_DIR}"
    echo "  备份目录: ${BACKUP_DIR}"
    echo ""
    
    # 确认继续
    if [ "$SKIP_CONFIRM" = false ]; then
        read -p "是否继续更新? (y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            show_warning "更新已取消"
            exit 0
        fi
    fi
    
    echo ""
    
    # 显示更新模式信息
    if [ -n "$REGISTRY_IMAGE" ]; then
        echo -e "${CYAN}更新模式: Docker Registry${NC}"
        echo -e "${BLUE}目标镜像: ${REGISTRY_IMAGE}${NC}"
        echo ""
    fi
    
    # 执行更新步骤
    check_environment
    
    # 读取安装配置信息（性能模式等）
    read_installation_info
    
    # 自动选择更新模式
    auto_select_update_mode
    
    echo ""
    
    # 根据模式准备镜像
    if [ "$UPDATE_MODE" = "local" ]; then
        check_local_image
    elif [ "$UPDATE_MODE" = "registry" ]; then
        prepare_registry_image
    fi
    
    echo ""
    
    # 确认更新信息
    if [ "$SKIP_CONFIRM" = false ]; then
        echo -e "${YELLOW}更新信息确认:${NC}"
        echo "  更新模式: $UPDATE_MODE"
        if [ "$UPDATE_MODE" = "local" ]; then
            echo "  镜像文件: $NEW_IMAGE_FILE"
        else
            echo "  目标镜像: $NEW_IMAGE_NAME"
        fi
        echo ""
        read -p "确认开始更新? (y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            show_warning "更新已取消"
            exit 0
        fi
    fi
    
    echo ""
    
    # 备份数据
    if [ "$SKIP_BACKUP" = false ]; then
        backup_data
    else
        show_info "跳过备份步骤"
    fi
    
    # 停止服务
    stop_services
    
    # 加载或拉取新镜像
    load_new_image
    
    # 更新配置
    update_config
    
    echo ""
    
    # 检查并初始化对象存储（老用户适配）
    # 重要：在启动服务前添加配置，这样容器启动时就能使用正确的密码
    check_and_init_object_storage
    
    echo ""
    
    # 启动服务
    start_services
    
    echo ""
    
    # 验证 RustFS 密码配置
    verify_rustfs_password
    
    echo ""
    
    # 数据库迁移
    if [ "$SKIP_MIGRATE" = false ]; then
        run_migrations
    else
        show_info "跳过数据库迁移"
    fi
    
    # 验证更新
    verify_update
    
    echo ""
    
    # 文件迁移到对象存储（如果设置了参数）
    if [ "$AUTO_MIGRATE_MEDIA" = true ]; then
        # 检查是否启用了对象存储且有本地文件
        STORAGE_ENABLED=$(grep "^SNOW_USE_OBJECT_STORAGE=" .env | cut -d'=' -f2 2>/dev/null || echo "False")
        if [ "$STORAGE_ENABLED" = "True" ]; then
            if [ -d "web/media" ] && [ "$(find web/media -type f 2>/dev/null | wc -l)" -gt 0 ]; then
                show_step "执行文件迁移到对象存储..."
                sleep 3
                migrate_media_to_storage
            else
                show_info "web/media 目录为空，无需迁移"
            fi
        else
            show_warning "对象存储未启用，跳过文件迁移"
        fi
        echo ""
    fi
    
    # 清理旧镜像
    if [ "$AUTO_CLEANUP" = true ]; then
        # 自动清理，不询问
        show_info "自动清理旧镜像..."
        docker image prune -f 2>/dev/null || true
    else
        cleanup_old_images
    fi
    
    # 显示完成信息
    show_completion
}

# 执行主函数
main "$@"