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

当前 Catalog 内置 Maven 4.0.0、MyBatis 3.0.5、Java EE Web Application 2.5/3.0、
Spring Framework 3.0.6 和 Apache CXF 2.6.1 所需的离线 DTD/XSD，并包含这些约束继续
引用的传递依赖。常用约束同时映射 HTTP、HTTPS；Spring 同时支持带 `3.0` 版本号和不带
版本号的 URL。新增 DTD/XSD 时，应把文件放到 `xml/` 下，并同步更新
`xml/catalog.xml` 的映射；存在 `xsd:import`、`xsd:include` 或 DTD 实体引用时，还必须
一并收录其传递依赖。

约束文件按发行方或规范族分组，目录约定如下：

| 目录 | 用途 |
| --- | --- |
| `xml/dtd/mybatis/` | MyBatis 配置文件和 Mapper DTD |
| `xml/xsd/apache-maven/` | Apache Maven POM XSD |
| `xml/xsd/apache-cxf/` | Apache CXF JAX-WS 及配置 XSD |
| `xml/xsd/java-ee/` | Java EE Web、JSP、公共 XSD 及其内部 DTD 依赖 |
| `xml/xsd/spring-framework/` | Spring Framework 各命名空间 XSD |

`java-ee/` 中的 `XMLSchema.dtd` 和 `datatypes.dtd` 必须与 `xml.xsd` 保持在同一目录，
因为上游约束使用相对路径引用它们。它们作为 Java EE/W3C Schema 依赖整体管理，不移动到
`xml/dtd/`。新增发行方时应建立新的子目录，不要把约束文件直接放在 `dtd/` 或 `xsd/` 根目录。

当前 `packager.jsonc` 中的所有 Profile 都共享 Default settings；XML Catalog 通过该设置
全局注册。其余 Profile 资源通过 `use_default` 共享。
如需让某个 Profile 使用独立文件，可在本目录增加文件，再把
`settings.profiles` 或 `resources.profiles` 中该 Profile 的配置改为相对路径。

注意：这些文件会被提交到仓库并进入发布产物。不要写入访问令牌、密码、私钥或其他敏感信息。
不需要某类资源时，应从 `packager.jsonc` 的 `resources.default` 删除；除 `xml` 外，还应
删除各 Profile 对应的 `use_default` 项。
