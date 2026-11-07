# NAS Cloudflare 回源路由

多网卡 NAS 可能用源地址策略表选择出口。Docker 发布端口将请求 DNAT 到容器地址后，策略表中若只有局域网和默认路由，容器流量会错误发往网关。Cloudflare Tunnel 正常连接，但回源连接超时。

本次 Azuki 上 `192.168.5.6:18080` 出现此问题。容器健康且直接访问容器返回 200；源地址策略表 11 把容器地址发往 `192.168.5.1`。优先级 9 的目标网段规则使 Tube2Bili 网桥使用主路由表中的直连路由，保留原有默认出口和 SSH。

安装到 NAS（以 root 执行）：

```sh
install -m 755 scripts/maintain_nas_routes.py /usr/local/sbin/tube2bili-routing
install -m 644 deploy/tube2bili-routing.service deploy/tube2bili-routing.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tube2bili-routing.timer
systemctl start tube2bili-routing.service
```

脚本每分钟从 `tube2bili-app-1` 的 Docker 网络读取 IPv4 网段，只为已有主表直连路由添加规则；恢复被系统重置的规则，并替换容器重建后的旧网段。优先级 9 已被无关规则占用时停止，不覆盖它们。失败会在 journal 中记录并在下次定时执行时重试。

检查：

```sh
systemctl status tube2bili-routing.timer
journalctl -u tube2bili-routing.service --no-pager -n 10
ip rule show
curl --noproxy '*' --max-time 10 http://192.168.5.6:18080/healthz
```

回滚：

```sh
systemctl disable --now tube2bili-routing.timer
/usr/local/sbin/tube2bili-routing --remove
```

规则归属保存在 `/var/lib/tube2bili-routing/subnets.json`。本次修正无需重启 NAS 或 Tunnel；没有修改 Tunnel Token、公开域名或应用凭证。
