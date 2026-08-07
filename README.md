# Dev Forge

将指定版本的 Windows VS Code、与该版本兼容的最新稳定版扩展、当前环境的
`settings.json` 打包为一个可校验、可离线安装的 ZIP 文件。

## 能力

- 下载指定 `major.minor.patch` 版本的 VS Code Windows 安装包或 ZIP 包；
- 从 Visual Studio Marketplace 为每个扩展选择：
  - 非预发布版本；
  - `engines.vscode` 与目标 VS Code 兼容；
  - 优先匹配目标 Windows 架构，其次选择通用包；
- 自动定位 macOS、Linux、Windows 当前用户的 VS Code `settings.json`，也支持显式指定；
- 生成文件哈希、版本清单和离线安装脚本；
- 输出一个结构固定的 ZIP 压缩包。

## 快速开始

需要 Python 3.10 或更高版本，无第三方运行时依赖。

```bash
python3 -m pip install -e .
# 编辑版本和扩展列表
dev-forge --config packager.jsonc
```

不希望安装命令行入口时，也可以直接指定源码目录运行：

```bash
PYTHONPATH=src python3 -m dev_forge --config packager.jsonc
```

默认在 `dist/` 中生成：

```text
dev-forge-1.95.3-win32-x64/
├── README.txt
├── install.ps1
├── manifest.json
├── vscode/
│   └── VSCodeSetup-x64-1.95.3.exe
├── extensions/
│   ├── ms-python.python-<version>.vsix
│   └── dbaeumer.vscode-eslint-<version>.vsix
└── user-data/
    └── settings.json
```

同目录还会生成 `dev-forge-1.95.3-win32-x64.zip`。成功后，中间文件夹默认被保留；使用 `--archive-only` 可只保留 ZIP。

## 完整配置参考

配置文件使用 UTF-8 编码，支持 JSONC 风格的 `// 单行注释` 和
`/* 块注释 */`，但不支持尾随逗号。字符串中的 `//`（例如 URL）不会被当成注释。
完整结构如下：

```json
{
  "vscode": {
    "version": "1.95.3",
    "package": "system",
    "arch": "x64"
  },
  "extensions": [
    "ms-python.python",
    "dbaeumer.vscode-eslint"
  ],
  "settings": "auto",
  "output_dir": "dist"
}
```

### 配置项总览

| 配置项 | 类型 | 必填 | 默认值 | 允许值或格式 |
| --- | --- | --- | --- | --- |
| `vscode` | 对象 | 是 | 无 | VS Code 下载配置 |
| `vscode.version` | 字符串 | 是 | 无 | 完整的 `major.minor.patch`，如 `1.95.3` |
| `vscode.package` | 字符串 | 否 | `system` | `system`、`user`、`archive` |
| `vscode.arch` | 字符串 | 否 | `x64` | `x64`、`arm64` |
| `extensions` | 字符串数组 | 否 | `[]` | 每项为 `publisher.name` |
| `settings` | 字符串 | 否 | `auto` | `auto` 或一个文件路径 |
| `output_dir` | 字符串 | 否 | `dist` | 一个目录路径 |

### `vscode`

VS Code Windows 发行包的下载配置。该对象本身以及 `version` 必须提供；
`package` 和 `arch` 可以省略并使用默认值。

#### `vscode.version`

- 类型：字符串。
- 必填：是。
- 格式：三个十进制数字段组成的完整版本号 `major.minor.patch`。
- 有效示例：`"1.95.3"`、`"1.100.0"`。
- 无效示例：`"latest"`、`"1.95"`、`"v1.95.3"`、`"1.95.3-insider"`。
- 作用：同时决定 VS Code 下载版本和扩展的兼容性筛选基准。
- 限制：当前只支持明确的稳定版本，不支持 `latest`、Insiders 或版本范围。

程序会使用该版本构造微软 Stable 下载地址。如果版本格式正确但微软没有发布该版本，
会在下载阶段返回错误。

#### `vscode.package`

- 类型：字符串。
- 必填：否。
- 默认值：`"system"`。
- 可选值：
  - `"system"`：Windows System Installer，通常用于为整台计算机安装；输出 `.exe`。
  - `"user"`：Windows User Installer，安装到当前用户目录；输出 `.exe`。
  - `"archive"`：Windows ZIP 发行包，无安装程序；输出 `.zip`。
- 值区分大小写，`"System"` 等写法无效。

当选择 `archive` 时，生成的 `install.ps1` 会把 VS Code 自动解压到包内的
`vscode\app` 目录，再使用其中的 `code.cmd` 安装扩展。

#### `vscode.arch`

- 类型：字符串。
- 必填：否。
- 默认值：`"x64"`。
- 可选值：
  - `"x64"`：64 位 Intel/AMD Windows。
  - `"arm64"`：64 位 ARM Windows。
- 作用：决定 VS Code 安装包架构，并优先选择同架构的扩展 VSIX。
- 扩展没有平台专用构建时，会选择该扩展的通用 VSIX。

`package` 与 `arch` 的组合全部受支持：

| `package` | `arch` | 微软下载目标 | 文件类型 |
| --- | --- | --- | --- |
| `system` | `x64` | `win32-x64` | `.exe` |
| `system` | `arm64` | `win32-arm64` | `.exe` |
| `user` | `x64` | `win32-x64-user` | `.exe` |
| `user` | `arm64` | `win32-arm64-user` | `.exe` |
| `archive` | `x64` | `win32-x64-archive` | `.zip` |
| `archive` | `arm64` | `win32-arm64-archive` | `.zip` |

### `extensions`

- 类型：字符串数组。
- 必填：否。
- 默认值：空数组 `[]`，即不下载扩展。
- 每项格式：Marketplace 扩展 ID `publisher.name`。
- 示例：`"ms-python.python"`、`"esbenp.prettier-vscode"`。
- ID 不区分大小写，读取后会转成小写；重复 ID 会保留第一次并自动去重。
- ID 只能包含字母、数字、下划线、连字符和点，并且必须包含分隔 publisher 与 name 的第一个点。

对于列表中的每个 ID，程序会读取 Marketplace 的全部可用版本，然后：

1. 排除预发布版本；
2. 按扩展的 `engines.vscode` 排除与目标 VS Code 不兼容的版本；
3. 排除为其他操作系统或架构构建的 VSIX；
4. 在剩余版本中选择版本号最高的稳定版本；
5. 同版本存在多个构建时，优先选择 `win32-x64` 或 `win32-arm64`，其次选择通用构建。

当前不支持在配置中固定某个扩展版本。程序只下载显式列出的 ID，也不会自动展开
Extension Pack、`extensionDependencies` 或 `extensionPack` 成员。完全离线使用时，应把这些依赖也加入数组。

可用下面的命令获取本机已安装扩展的 ID：

```bash
code --list-extensions
```

### `settings`

- 类型：字符串。
- 必填：否。
- 默认值：`"auto"`。
- 可选形式：精确值 `"auto"`，或一个现有文件的路径。
- `auto` 区分大小写。

`"auto"` 按运行机器的操作系统查找：

| 操作系统 | 默认查找位置 |
| --- | --- |
| Windows | `%APPDATA%\Code\User\settings.json` |
| macOS | `~/Library/Application Support/Code/User/settings.json` |
| Linux | `$XDG_CONFIG_HOME/Code/User/settings.json`；未设置变量时使用 `~/.config/Code/User/settings.json` |

自定义路径支持绝对路径、相对于配置文件所在目录的路径以及 `~` 用户目录写法，例如：

```json
"settings": "./profiles/frontend-settings.json"
```

源文件必须存在并且是普通文件，否则打包会终止。程序原样复制内容，不会解析、合并或修改
JSON/JSONC。若需要空设置，可指定一个内容为 `{}` 的文件。

### `output_dir`

- 类型：字符串。
- 必填：否。
- 默认值：`"dist"`。
- 支持绝对路径、相对于配置文件所在目录的路径以及 `~` 用户目录写法。
- 目录不存在时自动创建。

例如目标版本为 `1.95.3`、架构为 `x64`，输出名称固定为：

```text
<output_dir>/dev-forge-1.95.3-win32-x64/
<output_dir>/dev-forge-1.95.3-win32-x64.zip
```

为避免覆盖已有文件，如果同名文件夹或 ZIP 已经存在，程序会停止并报错。配置中暂不支持
自定义产物名称。

## 命令行参数

```text
dev-forge [-h]
                        [--config CONFIG]
                        [--settings SETTINGS]
                        [--output-dir OUTPUT_DIR]
                        [--archive-only]
```

| 参数 | 是否需要值 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `-h`, `--help` | 否 | — | 显示帮助并退出 |
| `--config` | 是 | `packager.jsonc` | 配置文件路径；相对路径基于当前工作目录 |
| `--settings` | 是 | 配置中的值 | 覆盖 `settings`；相对路径基于配置文件所在目录 |
| `--output-dir` | 是 | 配置中的值 | 覆盖 `output_dir`；相对路径基于配置文件所在目录 |
| `--archive-only` | 否 | 关闭 | 成功生成 ZIP 后删除未压缩的中间文件夹，只保留 ZIP |

命令行没有提供 `version`、`package`、`arch` 或 `extensions` 的覆盖参数，这些项目需要在
JSON 配置文件中修改。

示例：

```bash
dev-forge \
  --config ./packager.jsonc \
  --settings ./profiles/settings.json \
  --output-dir ./release \
  --archive-only
```

## 离线恢复

将 ZIP 解压到 Windows 后，以 PowerShell 运行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install.ps1
```

脚本会安装 VS Code（ZIP 发行包会自动解压）、逐个安装 VSIX，并把设置复制到
`$env:APPDATA\Code\User\settings.json`。如需覆盖已有设置，使用
`.\install.ps1 -ForceSettings`。脚本不会静默覆盖已有设置。

## 开发与测试

```bash
python3 -m unittest discover -s tests -v
```

Marketplace 下载接口不需要令牌。网络请求会重试，下载先写入 `.part` 文件，完成后再原子替换。
