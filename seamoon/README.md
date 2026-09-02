# 信息收集工作台 SeaMoon Core

> 最后更新：2026-09-01

本目录包含信息收集工作台使用的最小 SeaMoon 兼容运行时。代码基于 DVKunion/SeaMoon 提交 `21638db0a36f6b5531078c4769aedb1c1ad93c1c` 提取并适配，遵循 MIT 许可证；来源与许可见 `NOTICE.md`。

## 保留能力

- 本地 HTTP 代理网关。
- WebSocket 隧道。
- 云函数端 HTTP/HTTPS 转发。
- 阿里云函数计算 FC 部署、更新、复用和删除。
- 腾讯云函数 SCF 部署、更新、复用和删除。

原项目的独立 UI、本地数据库、SOCKS、Tor、V2Ray、gRPC 和公共代理池功能未集成。

## 命令

```bash
# 本地 HTTP 代理与管理 API
seamoon-core gateway -proxy 0.0.0.0:19080 -admin 0.0.0.0:19081

# 云函数 WebSocket HTTP 转发服务
seamoon-core server -p 9000 -t websocket

# 云平台操作；JSON 从 stdin 读取
seamoon-core cloud deploy
seamoon-core cloud destroy
```

## 连接模型

- 本地网关接收一个普通 HTTP 代理 TCP 连接。
- 网关从已配置的函数地址列表中轮询选择一个节点；首个节点连接失败时会尝试其余节点。
- 网关为该连接新建一个到云函数 `/http` 的 WebSocket。
- 云函数读取代理请求，连接目标主机并双向转发字节流。
- 一个代理 TCP 连接对应一个 WebSocket 隧道。

因此，同一批请求使用相同函数地址并不保证固定出口IP。若业务需要固定出口，应在云平台配置 NAT/EIP，而不是依赖函数实例复用。

## 托管规格

平台一键部署使用：

- CPU：0.1 vCPU
- 内存：128 MB
- 磁盘：512 MB
- 单实例并发：6
- 预置/最小实例：0（scale-to-zero）

信息收集工作台的 ICP 业务层每批最多并发5家企业，为实例保留1个连接槽位；爱企查每15次实际 HTTP 请求关闭本地客户端并建立新隧道。云函数批次之间不使用直连IP的全局12秒冷却。隧道重建不保证云平台一定更换公网出口IP。

## 镜像

平台支持地域使用预置镜像，正常的一键部署无需用户填写镜像地址。只有维护自定义运行时或手工部署时才需要构建：

```bash
docker build \
  -f seamoon/Dockerfile.function \
  -t your-registry/seamoon-core:latest \
  seamoon
```

## 测试

```bash
cd seamoon
go test ./...
```
