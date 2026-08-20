# VS Code 打包配置

此目录是 GitHub Actions 和本地打包共同使用的 VS Code 配置源。修改后提交到 Git，
下一次运行发布工作流时就会进入离线包，不需要 GitHub runner 访问开发机。

文件与安装目标的对应关系：

| 仓库文件 | Windows 安装目标 |
| --- | --- |
| `settings.json` | Default Profile 的 `settings.json` |
| `xml/` | 全局 XML Catalog 及其离线 DTD/XSD；必须包含 `catalog.xml` |
| `keybindings.json` | Default Profile 的 `keybindings.json` |
| `snippets/` | Default Profile 的 `snippets/` |
| `tasks.json` | Default Profile 的 `tasks.json` |
| `mcp.json` | Default Profile 的 `mcp.json` |

当前 Catalog 内置 Maven 4.0.0 XSD（旧地址、HTTP、HTTPS）和 MyBatis 3 Mapper DTD
（HTTP）。新增 DTD/XSD 时，应把文件放到 `xml/` 下，并同步更新 `xml/catalog.xml` 的映射。

当前 `packager.jsonc` 中的所有 Profile 都共享 Default settings；XML Catalog 通过该设置
全局注册。其余 Profile 资源通过 `use_default` 共享。
如需让某个 Profile 使用独立文件，可在本目录增加文件，再把
`settings.profiles` 或 `resources.profiles` 中该 Profile 的配置改为相对路径。

注意：这些文件会被提交到仓库并进入发布产物。不要写入访问令牌、密码、私钥或其他敏感信息。
不需要某类资源时，应从 `packager.jsonc` 的 `resources.default` 删除；除 `xml` 外，还应
删除各 Profile 对应的 `use_default` 项。
