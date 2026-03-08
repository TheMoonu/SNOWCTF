# Docker 配置工具脚本集

这里存放了方便设置和管理 Docker 的实用脚本。

PS：以下操作都在远程题目服务器上进行,您可以直接将脚本直接scp至远程题目服务器。

## 📋 脚本列表

### 1. enable_docker_remote_tls.sh
**功能**：一键开启 Docker 远程访问并配置 TLS 加密证书

**用途**：
- 自动生成 TLS 证书（CA、服务端、客户端）
- 配置 Docker daemon 支持远程 TLS 访问
- 自动配置防火墙规则
- 打包客户端证书供远程使用

**使用方法**：
```bash
# 添加执行权限
chmod +x enable_docker_remote_tls.sh

# 运行脚本（需要 root 权限）
sudo ./enable_docker_remote_tls.sh <服务器IP或域名> [端口]

# 示例
sudo ./enable_docker_remote_tls.sh 192.168.1.100
sudo ./enable_docker_remote_tls.sh 192.168.1.100 2376
sudo ./enable_docker_remote_tls.sh example.com 2376
```

**配置完成后**：
- 服务器证书位置：`/etc/docker/certs/`
- 客户端证书包：`/tmp/docker-client-certs-<IP>.tar.gz`

### 2. fix_docker_tls.sh
**功能**：快速修复 Docker TLS 配置导致的启动失败问题

**用途**：
- 修复 daemon.json 与 systemd service 冲突
- 自动诊断 Docker 启动问题
- 提供详细的错误信息和解决方案

**使用方法**：
```bash
# 添加执行权限
chmod +x fix_docker_tls.sh

# 运行修复脚本（需要 root 权限）
sudo ./fix_docker_tls.sh
```


## 📝 注意事项

1. **权限要求**：所有脚本都需要 root 权限（使用 sudo）
2. **网络要求**：配置远程访问需要确保防火墙规则正确
3. **证书安全**：客户端证书包含敏感信息，请妥善保管
4. **系统支持**：支持 Ubuntu/Debian/CentOS/RHEL/Fedora/Rocky/AlmaLinux
5. **临时文件**：客户端证书保存在 `/tmp` 目录，请及时下载

## 🔒 安全建议

- ✅ 使用强密码保护服务器
- ✅ 配置防火墙，仅允许可信 IP 访问 Docker 端口
- ✅ 定期检查和更新证书
- ✅ 不要将证书文件提交到版本控制系统
- ✅ 妥善保管客户端证书（`~/.docker/*.pem`）

## 🛠️ 故障排查

### Docker 无法启动
```bash
# 查看详细错误日志
sudo journalctl -xeu docker

# 检查配置文件
cat /etc/docker/daemon.json
cat /etc/systemd/system/docker.service.d/override.conf

# 运行修复脚本
sudo ./fix_docker_tls.sh
```

### 无法远程连接
```bash
# 检查端口是否监听
sudo ss -tuln | grep 2376

# 检查防火墙
sudo firewall-cmd --list-ports  # CentOS/RHEL
sudo ufw status                 # Ubuntu/Debian

# 测试本地连接
docker --tlsverify \
  --tlscacert=/etc/docker/certs/ca.pem \
  --tlscert=/etc/docker/certs/client.pem \
  --tlskey=/etc/docker/certs/client-key.pem \
  -H=tcp://127.0.0.1:2376 version
```

