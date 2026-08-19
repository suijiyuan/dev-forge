# VS Code 打包配置

此目录是 GitHub Actions 和本地打包共同使用的 VS Code 配置源。修改后提交到 Git，
下一次运行发布工作流时就会进入离线包，不需要 GitHub runner 访问开发机。

文件与安装目标的对应关系：

| 仓库文件 | Windows 安装目标 |
| --- | --- |
| `settings.json` | Default Profile 的 `settings.json` |
| `keybindings.json` | Default Profile 的 `keybindings.json` |
| `snippets/` | Default Profile 的 `snippets/` |
| `tasks.json` | Default Profile 的 `tasks.json` |
| `mcp.json` | Default Profile 的 `mcp.json` |

当前 `packager.jsonc` 中的所有 Profile 都通过 `use_default` 共享这些 Default 资源。
如需让某个 Profile 使用独立文件，可在本目录增加文件，再把
`settings.profiles` 或 `resources.profiles` 中该 Profile 的配置改为相对路径。

注意：这些文件会被提交到仓库并进入发布产物。不要写入访问令牌、密码、私钥或其他敏感信息。
不需要某类资源时，应同时从 `packager.jsonc` 的 `resources.default` 和各 Profile 的对应
`use_default` 项中删除该资源配置。
