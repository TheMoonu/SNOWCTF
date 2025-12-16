#!/bin/bash

# ============================================
# SecSnow Media 文件迁移到 MinIO 脚本
# ============================================
# 功能：将本地 media 目录的文件迁移到 MinIO
# 作者：SecSnow Team
# ============================================

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 配置
MEDIA_DIR="./web/media"
MINIO_CONTAINER="secsnow-minio"
MINIO_BUCKET="secsnow"

# 打印函数
print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 检查 MinIO 服务
check_minio() {
    print_info "检查 MinIO 服务状态..."
    
    if ! docker ps | grep -q "$MINIO_CONTAINER"; then
        print_error "MinIO 服务未运行，请先启动 MinIO"
        echo "运行：docker-compose up -d minio"
        exit 1
    fi
    
    print_success "MinIO 服务运行正常"
}

# 检查 media 目录
check_media_dir() {
    print_info "检查 media 目录..."
    
    if [ ! -d "$MEDIA_DIR" ]; then
        print_error "Media 目录不存在: $MEDIA_DIR"
        exit 1
    fi
    
    # 统计文件信息
    FILE_COUNT=$(find "$MEDIA_DIR" -type f | wc -l)
    DIR_SIZE=$(du -sh "$MEDIA_DIR" | cut -f1)
    
    print_info "找到 $FILE_COUNT 个文件，总大小: $DIR_SIZE"
}

# 显示文件列表
show_file_list() {
    print_info "Media 目录结构："
    echo ""
    tree "$MEDIA_DIR" -L 2 -h || ls -lhR "$MEDIA_DIR" | head -50
    echo ""
}

# 备份 media 目录
backup_media() {
    print_info "创建备份..."
    
    BACKUP_FILE="media_backup_$(date +%Y%m%d_%H%M%S).tar.gz"
    tar -czf "$BACKUP_FILE" "$MEDIA_DIR"
    
    print_success "备份已创建: $BACKUP_FILE"
}

# 迁移文件到 MinIO
migrate_to_minio() {
    print_info "开始迁移文件到 MinIO..."
    
    # 从 .env 读取 MinIO 认证信息
    if [ -f .env ]; then
        source .env
    fi
    
    MINIO_USER=${MINIO_ROOT_USER:-minioadmin}
    MINIO_PASSWORD=${MINIO_ROOT_PASSWORD:-minioadmin123456}
    
    # 使用 mc 客户端迁移
    docker run -it --rm \
        -v "$(pwd)/$MEDIA_DIR:/media" \
        --network=secsnow-network \
        minio/mc:latest sh -c "
            echo '配置 MinIO 客户端...';
            mc alias set secsnow http://minio:9000 $MINIO_USER '$MINIO_PASSWORD';
            
            echo '检查 bucket...';
            mc ls secsnow/$MINIO_BUCKET || mc mb secsnow/$MINIO_BUCKET;
            
            echo '开始上传文件...';
            mc cp --recursive /media/ secsnow/$MINIO_BUCKET/;
            
            echo '设置 bucket 为公开访问...';
            mc anonymous set public secsnow/$MINIO_BUCKET;
            
            echo '验证上传结果...';
            mc ls secsnow/$MINIO_BUCKET/;
        "
    
    if [ $? -eq 0 ]; then
        print_success "文件迁移成功！"
        return 0
    else
        print_error "文件迁移失败"
        return 1
    fi
}

# 验证迁移结果
verify_migration() {
    print_info "验证迁移结果..."
    
    # 从 .env 读取配置
    if [ -f .env ]; then
        source .env
    fi
    
    MINIO_USER=${MINIO_ROOT_USER:-minioadmin}
    MINIO_PASSWORD=${MINIO_ROOT_PASSWORD:-minioadmin123456}
    
    # 统计 MinIO 中的文件数量
    MINIO_FILE_COUNT=$(docker run --rm \
        --network=secsnow-network \
        minio/mc:latest sh -c "
            mc alias set secsnow http://minio:9000 $MINIO_USER '$MINIO_PASSWORD';
            mc ls --recursive secsnow/$MINIO_BUCKET/ | wc -l
        ")
    
    LOCAL_FILE_COUNT=$(find "$MEDIA_DIR" -type f | wc -l)
    
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  迁移验证"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "本地文件数量: $LOCAL_FILE_COUNT"
    echo "MinIO文件数量: $MINIO_FILE_COUNT"
    echo ""
    
    if [ "$MINIO_FILE_COUNT" -ge "$LOCAL_FILE_COUNT" ]; then
        print_success "验证通过！所有文件已成功迁移"
        return 0
    else
        print_warning "文件数量不匹配，请检查"
        return 1
    fi
}

# 启用 MinIO
enable_minio() {
    print_info "启用 MinIO 存储..."
    
    if [ -f .env ]; then
        # 检查是否已启用
        if grep -q "^SNOW_USE_MINIO=True" .env; then
            print_info "MinIO 已启用"
        else
            # 修改配置
            sed -i.bak 's/^SNOW_USE_MINIO=.*/SNOW_USE_MINIO=True/' .env
            print_success "已修改 .env 文件，启用 MinIO"
        fi
    else
        print_error ".env 文件不存在"
        return 1
    fi
}

# 重启服务
restart_services() {
    print_info "重启服务以应用更改..."
    
    docker-compose restart web celery-worker celery-beat
    
    print_success "服务已重启"
}

# 保留本地备份（可选）
keep_local_backup() {
    print_warning "建议保留本地 media 目录作为备份"
    echo ""
    read -p "是否重命名本地 media 目录为 media.backup？(y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        mv "$MEDIA_DIR" "${MEDIA_DIR}.backup"
        mkdir -p "$MEDIA_DIR"
        print_success "本地目录已重命名为: ${MEDIA_DIR}.backup"
    else
        print_info "保留原 media 目录"
    fi
}

# 主函数
main() {
    clear
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  SecSnow Media 文件迁移到 MinIO"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    
    # 1. 检查环境
    check_minio
    check_media_dir
    
    # 2. 显示文件列表
    show_file_list
    
    # 3. 确认迁移
    echo ""
    print_warning "即将开始迁移，请确认以下信息："
    echo "  - 源目录: $MEDIA_DIR"
    echo "  - 目标: MinIO ($MINIO_BUCKET bucket)"
    echo "  - 将创建备份文件"
    echo ""
    read -p "是否继续？(y/n): " -n 1 -r
    echo
    
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_info "已取消迁移"
        exit 0
    fi
    
    # 4. 执行迁移流程
    backup_media
    migrate_to_minio
    
    # 5. 验证结果
    if verify_migration; then
        # 6. 启用 MinIO
        enable_minio
        
        # 7. 重启服务
        restart_services
        
        # 8. 处理本地备份
        keep_local_backup
        
        # 9. 完成
        echo ""
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo "  🎉 迁移完成！"
        echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        echo ""
        print_success "所有文件已成功迁移到 MinIO"
        print_info "访问 MinIO 控制台: http://$(hostname -I | awk '{print $1}'):7901"
        print_info "新上传的文件将自动保存到 MinIO"
        echo ""
    else
        print_error "迁移验证失败，请检查日志"
        exit 1
    fi
}

# 运行主函数
main

