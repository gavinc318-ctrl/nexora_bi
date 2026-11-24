# 假投递网关（开发期工具，不交付）

替代 LD-01 10.2 的统一投递服务（尚未建），用于在开发机上验收「告警能送到配置的收件人」（DD-100 第三块）。
**它不发任何短信或邮件**，只把要发的内容按收件人、按渠道写进文件。

由 `infra/playbooks/56-alerting.yml` 部署为 `oss911-fake-gateway.service`（账号 `oss911gw`，监听 `10.89.10.1:9196`），
不用手工起。接口与文件见 `server.py` 开头的说明；投递服务建成后应提供同样的三个接口，上游不用改。

单元测试（标准库，不需要 venv）：

```bash
cd test/fake_gateway && python3 -m unittest test_fake_gateway
```
