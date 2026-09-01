# SeaMoon core attribution

The WebSocket tunnel, HTTP forwarding, and cloud-function deployment design in
this directory is a focused adaptation of DVKunion/SeaMoon, commit
`21638db0a36f6b5531078c4769aedb1c1ad93c1c` (retrieved 2026-09-01).

SeaMoon is licensed under the MIT License. The upstream license text is kept in
`LICENSE.seamoon`.

This extraction intentionally omits SeaMoon's standalone UI, database, SOCKS,
Tor, V2Ray, gRPC, and proxy-pool features. It retains only the pieces needed by
信息收集工作台：

- local HTTP proxy to WebSocket tunnel bridging;
- cloud-function WebSocket HTTP forwarding server;
- Alibaba Cloud FC and Tencent Cloud SCF deployment adapters.
