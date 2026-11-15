"""进程内自建 MCP 桥接。把企业微信 / 飞书 OpenAPI 封装成标准 MCP 工具。

本模块同时实现 MCP server 端（JSON-RPC over Streamable HTTP）与一个简化的
OAuth 2.0 授权服务器，使现有 MCP 客户端（app/mcp/service.py）无需改动即可连接。
企微/飞书的应用凭据是平台统一配置（.env），不随用户变化。
"""
