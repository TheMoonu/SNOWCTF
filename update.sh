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
    
    # 只停止 web 相关服务，保留数据库
    show_info "停止 Web 服务..."
    $COMPOSE_CMD stop web celery celery-beat 2>/dev/null || true
    
    # 移除旧容器（保留数据卷）
    show_info "移除旧容器..."
    $COMPOSE_CMD rm -f web celery celery-beat 2>/dev/null || true
    
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
        # 更新 .env 文件中的镜像版本
        if grep -q "SECSNOW_IMAGE=" .env; then
            # 备份当前配置
            cp .env .env.pre_update
            
            # 更新镜像配置
            sed -i "s|^SECSNOW_IMAGE=.*|SECSNOW_IMAGE=${NEW_IMAGE_NAME}|" .env
            
            show_success "已更新 SECSNOW_IMAGE 为: ${NEW_IMAGE_NAME}"
        else
            # 如果没有该配置项，添加它
            echo "" >> .env
            echo "# 更新于 ${UPDATE_DATE}" >> .env
            echo "SECSNOW_IMAGE=${NEW_IMAGE_NAME}" >> .env
            show_info "已添加 SECSNOW_IMAGE 配置"
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
    mkdir -p minio/data 2>/dev/null || true
    mkdir -p web/media 2>/dev/null || true
    mkdir -p web/static 2>/dev/null || true
    mkdir -p web/log 2>/dev/null || true
    mkdir -p web/log/nginx 2>/dev/null || true
    mkdir -p web/whoosh_index 2>/dev/null || true
    mkdir -p nginx/ssl 2>/dev/null || true
    
    # 设置 MinIO 目录权限
    chmod -R 755 minio 2>/dev/null || true
    
    COMPOSE_CMD=$(get_compose_command)
    
    # 拉起所有服务
    show_info "启动所有服务..."
    if $COMPOSE_CMD up -d; then
        show_success "服务启动成功"
    else
        show_error "服务启动失败，请检查日志"
    fi
    
    # 等待服务就绪
    show_info "等待服务完全启动..."
    sleep 10
    
    # 显示服务状态
    show_info "服务状态："
    $COMPOSE_CMD ps
}

# 检查并初始化 MinIO（老用户适配）
check_and_init_minio() {
    show_step "检查 MinIO 配置..."
    
    # 检查 .env 中是否有 MinIO 配置
    if ! grep -q "SNOW_USE_MINIO" .env 2>/dev/null; then
        show_info "检测到旧版本配置，添加 MinIO 配置..."
        
        # 添加 MinIO 配置到 .env
        cat >> .env << 'EOF'

# ================================================
# 📦 MinIO 对象存储配置（新增）
# ================================================
SNOW_USE_MINIO=False
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin123456
MINIO_BUCKET_NAME=secsnow
MINIO_DATA_DIR=./minio/data
MINIO_API_PORT=7900
MINIO_CONSOLE_PORT=7901
SNOW_MINIO_USE_SSL=False
SNOW_MINIO_VERIFY_SSL=False
SNOW_MINIO_CUSTOM_DOMAIN=
MINIO_IMAGE=minio/minio:latest
MINIO_MC_IMAGE=minio/mc:latest
EOF
        show_success "MinIO 配置已添加"
        show_info "MinIO 默认为禁用状态，可稍后启用"
    fi
    
    # 检查是否启用了 MinIO
    MINIO_ENABLED=$(grep "^SNOW_USE_MINIO=" .env | cut -d'=' -f2)
    
    if [ "$MINIO_ENABLED" = "True" ]; then
        show_info "MinIO 已启用"
        
        # 检查 MinIO 服务是否在运行
        if ! docker ps | grep -q secsnow-minio; then
            show_warning "MinIO 服务未运行，将在启动服务时自动启动"
        else
            show_success "MinIO 服务运行正常"
        fi
        
        # 检查是否需要迁移 media 文件
        if [ -d "web/media" ] && [ "$(find web/media -type f | wc -l)" -gt 0 ]; then
            check_media_migration
        fi
    else
        show_info "MinIO 未启用（使用本地存储）"
        
        # 询问是否要启用 MinIO
        if [ "$AUTO_ENABLE_MINIO" != true ] && [ "$SKIP_CONFIRM" = false ]; then
            echo ""
            echo -e "${YELLOW}MinIO 是什么？${NC}"
            echo "  MinIO 是高性能的对象存储服务，用于存储上传的文件"
            echo "  优势：高可用、可扩展、支持分布式部署"
            echo ""
            read -p "是否启用 MinIO 对象存储？(y/n): " -n 1 -r
            echo
            
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                enable_minio_storage
            else
                show_info "保持使用本地存储"
            fi
        fi
    fi
}

# 检查是否需要迁移 media 文件到 MinIO
check_media_migration() {
    show_info "检查 media 文件迁移状态..."
    
    # 检查 MinIO 中是否已有文件
    MINIO_USER=$(grep "^MINIO_ROOT_USER=" .env | cut -d'=' -f2)
    MINIO_PASSWORD=$(grep "^MINIO_ROOT_PASSWORD=" .env | cut -d'=' -f2)
    MINIO_BUCKET=$(grep "^MINIO_BUCKET_NAME=" .env | cut -d'=' -f2)
    
    # 检查 MinIO 中的文件数量
    MINIO_FILE_COUNT=$(docker run --rm \
        --network=secsnow-network \
        minio/mc:latest sh -c "
            mc alias set secsnow http://minio:9000 ${MINIO_USER} '${MINIO_PASSWORD}' >/dev/null 2>&1;
            mc ls --recursive secsnow/${MINIO_BUCKET}/ 2>/dev/null | wc -l
        " 2>/dev/null || echo "0")
    
    LOCAL_FILE_COUNT=$(find web/media -type f | wc -l)
    
    echo ""
    echo -e "${YELLOW}文件迁移状态:${NC}"
    echo "  本地文件数: $LOCAL_FILE_COUNT"
    echo "  MinIO文件数: $MINIO_FILE_COUNT"
    
    # 如果 MinIO 中文件明显少于本地，提示迁移
    if [ "$MINIO_FILE_COUNT" -lt "$((LOCAL_FILE_COUNT / 2))" ]; then
        show_warning "MinIO 中文件数量较少，可能需要迁移"
        
        if [ "$AUTO_MIGRATE_MEDIA" = true ]; then
            migrate_media_to_minio
        elif [ "$SKIP_CONFIRM" = false ]; then
            echo ""
            read -p "是否现在迁移本地文件到 MinIO？(y/n): " -n 1 -r
            echo
            
            if [[ $REPLY =~ ^[Yy]$ ]]; then
                migrate_media_to_minio
            else
                show_info "跳过文件迁移"
                show_warning "可稍后运行: ./migrate_media_to_minio.sh"
            fi
        fi
    else
        show_success "文件已同步到 MinIO"
    fi
}

# 启用 MinIO 存储
enable_minio_storage() {
    show_step "启用 MinIO 对象存储..."
    
    # 修改 .env 配置
    sed -i.bak 's/^SNOW_USE_MINIO=.*/SNOW_USE_MINIO=True/' .env
    
    # 生成随机密码（如果是默认密码）
    CURRENT_PASSWORD=$(grep "^MINIO_ROOT_PASSWORD=" .env | cut -d'=' -f2)
    if [ "$CURRENT_PASSWORD" = "minioadmin123456" ]; then
        NEW_PASSWORD=$(openssl rand -base64 16 | tr -d '+/=' | head -c 20)
        sed -i "s/^MINIO_ROOT_PASSWORD=.*/MINIO_ROOT_PASSWORD=${NEW_PASSWORD}/" .env
        show_info "已生成随机 MinIO 密码"
    fi
    
    show_success "MinIO 已启用"
    show_info "MinIO 将在服务启动时自动运行"
}

# 迁移 media 文件到 MinIO
migrate_media_to_minio() {
    show_step "迁移 media 文件到 MinIO..."
    
    # 确保 MinIO 服务运行
    if ! docker ps | grep -q secsnow-minio; then
        show_warning "MinIO 未运行，先启动 MinIO..."
        docker-compose up -d minio 2>/dev/null || true
        sleep 5
    fi
    
    # 从 .env 读取配置
    MINIO_USER=$(grep "^MINIO_ROOT_USER=" .env | cut -d'=' -f2)
    MINIO_PASSWORD=$(grep "^MINIO_ROOT_PASSWORD=" .env | cut -d'=' -f2)
    MINIO_BUCKET=$(grep "^MINIO_BUCKET_NAME=" .env | cut -d'=' -f2)
    
    # 统计本地文件
    LOCAL_FILE_COUNT=$(find web/media -type f | wc -l)
    show_info "准备迁移 $LOCAL_FILE_COUNT 个文件..."
    
    # 使用 mc 客户端同步文件
    docker run --rm \
        -v "$(pwd)/web/media:/media" \
        --network=secsnow-network \
        minio/mc:latest sh -c "
            mc alias set secsnow http://minio:9000 ${MINIO_USER} '${MINIO_PASSWORD}';
            mc cp --recursive /media/ secsnow/${MINIO_BUCKET}/;
        " 2>&1 | grep -v "^$"
    
    if [ $? -eq 0 ]; then
        show_success "文件迁移完成"
        
        # 验证迁移结果
        MINIO_FILE_COUNT=$(docker run --rm \
            --network=secsnow-network \
            minio/mc:latest sh -c "
                mc alias set secsnow http://minio:9000 ${MINIO_USER} '${MINIO_PASSWORD}';
                mc ls --recursive secsnow/${MINIO_BUCKET}/ | wc -l
            " 2>/dev/null)
        
        echo ""
        echo -e "${GREEN}迁移结果:${NC}"
        echo "  本地文件: $LOCAL_FILE_COUNT"
        echo "  MinIO文件: $MINIO_FILE_COUNT"
        
        if [ "$MINIO_FILE_COUNT" -ge "$LOCAL_FILE_COUNT" ]; then
            show_success "✓ 所有文件已成功迁移"
            
            # 询问是否备份本地文件
            if [ "$SKIP_CONFIRM" = false ]; then
                echo ""
                read -p "是否将本地 media 目录重命名为 media.backup？(y/n): " -n 1 -r
                echo
                
                if [[ $REPLY =~ ^[Yy]$ ]]; then
                    mv web/media web/media.backup
                    mkdir -p web/media
                    show_success "本地目录已重命名为 media.backup"
                fi
            fi
        else
            show_warning "文件数量不匹配，请检查"
        fi
    else
        show_error "文件迁移失败"
    fi
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
    show_info "发现简历相关表，检查数据量..."
    for table in $RESUME_TABLES; do
        COUNT=$(docker exec secsnow-postgres psql -U secsnow -d secsnow -t -c "SELECT COUNT(*) FROM $table" 2>/dev/null | tr -d ' ')
        show_warning "  - $table: $COUNT 条数据"
    done
    
    # 询问是否删除（如果没有设置自动清理）
    if [ "$AUTO_CLEAN_RESUME" != true ]; then
        echo ""
        read -p "是否删除这些废弃的简历表？(y/n): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            show_info "跳过简历表清理"
            return
        fi
    fi
    
    # 删除表
    show_info "删除简历表..."
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
    echo "  备份目录: ${CURRENT_BACKUP_DIR:-未备份}"
    echo ""
    
    COMPOSE_CMD=$(get_compose_command)
    
    echo -e "${BLUE}常用命令:${NC}"
    echo "  查看服务状态:"
    echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD ps"
    echo ""
    echo "  查看 Web 日志:"
    echo "    docker logs -f secsnow-web"
    echo ""
    echo "  查看所有服务日志:"
    echo "    cd ${INSTALL_DIR} && $COMPOSE_CMD logs -f"
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
    
    echo -e "${BLUE}回滚步骤（如果需要）:${NC}"
    echo "  1. 停止服务: cd ${INSTALL_DIR} && $COMPOSE_CMD down"
    echo "  2. 恢复配置: cp ${CURRENT_BACKUP_DIR}/.env.backup .env"
    if [ "$UPDATE_MODE" = "registry" ]; then
        local image_base=$(echo "$REGISTRY_IMAGE" | cut -d':' -f1)
        echo "  3. 拉取旧版本: docker pull ${image_base}:<旧版本号>"
        echo "  4. 更新 .env 文件中的 SECSNOW_IMAGE"
    else
        echo "  3. 重新加载旧镜像"
    fi
    echo "  5. 启动服务: $COMPOSE_CMD up -d"
    echo ""
    
    # 检查 MinIO 状态并显示信息
    MINIO_STATUS=""
    if grep -q "^SNOW_USE_MINIO=True" .env 2>/dev/null; then
        MINIO_STATUS="${GREEN}已启用${NC}"
        MINIO_PORT=$(grep "^MINIO_CONSOLE_PORT=" .env | cut -d'=' -f2 || echo "7901")
        echo -e "${BLUE}MinIO 对象存储:${NC}"
        echo "  状态: 已启用"
        echo "  控制台: http://服务器IP:${MINIO_PORT}"
        echo "  密码: 查看 .env 中的 MINIO_ROOT_PASSWORD"
        echo ""
    fi
    
    echo -e "${YELLOW}提示:${NC}"
    echo "  1. 如遇问题，可查看日志: docker logs secsnow-web"
    echo "  2. 备份文件保存在: ${BACKUP_DIR}"
    echo "  3. 建议测试主要功能是否正常"
    echo "  4. 数据库数据已保留，无需担心数据丢失"
    if grep -q "^SNOW_USE_MINIO=True" .env 2>/dev/null; then
        echo "  5. MinIO 已启用，新上传文件将保存到对象存储"
        if [ -d "web/media.backup" ]; then
            echo "  6. 旧 media 文件已备份到 web/media.backup"
        fi
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
    echo "  --clean-resume                    自动清理废弃的简历表（不询问）"
    echo "  --enable-minio                    自动启用 MinIO（不询问）"
    echo "  --migrate-media                   自动迁移 media 文件到 MinIO（不询问）"
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
    echo "  $0 -y --cleanup --clean-resume"
    echo ""
    echo "  # 老用户启用 MinIO"
    echo "  $0 -y --enable-minio --migrate-media"
    echo ""
    echo "  # 从 Docker Hub 拉取"
    echo "  $0 -r secsnow/secsnow:v1.0.0"
    echo "  $0 -r secsnow/secsnow:latest -y --clean-resume"
    echo ""
    echo "  # 从私有 Harbor 拉取"
    echo "  $0 -r harbor.company.com/secsnow/secsnow:v1.0.0"
    echo "  $0 -r harbor.company.com/secsnow/secsnow:latest --cleanup --clean-resume"
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
    AUTO_ENABLE_MINIO=false
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
                AUTO_CLEAN_RESUME=true
                shift
                ;;
            --enable-minio)
                AUTO_ENABLE_MINIO=true
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
    
    # 启动服务
    start_services
    
    echo ""
    
    # 检查并初始化 MinIO（老用户适配）
    check_and_init_minio
    
    echo ""
    
    # 数据库迁移
    if [ "$SKIP_MIGRATE" = false ]; then
        run_migrations
    else
        show_info "跳过数据库迁移"
    fi
    
    # 验证更新
    verify_update
    
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
