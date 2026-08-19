# Dev Forge

将指定版本的 Windows VS Code、与该版本兼容的最新稳定版扩展，以及 VS Code Profile
资源打包为一个可校验、可离线安装的 ZIP 文件。

## 能力

- 下载指定 `major.minor.patch` 版本的 VS Code Windows 安装包或 ZIP 包；
- 从 Visual Studio Marketplace 为每个扩展选择：
  - 非预发布版本；
  - `engines.vscode` 与目标 VS Code 兼容；
  - 优先匹配目标 Windows 架构，其次选择通用包；
- 支持 Default 与 Profile 设置；Profile 可共享同一份 Default `settings.json`，也可使用独立文件；
- 支持 `keybindings.json`、`snippets/`、`tasks.json`、`mcp.json`，并可按资源继承 Default；
- 支持安全的 `merge` 和完全重建扩展的 `replace` 两种安装模式；
- 生成文件哈希、版本清单和离线安装脚本；
- 安装脚本按配置自动创建任意 Profile：通用扩展同时用于 Default 和所有 Profile，专属扩展仅安装到对应 Profile；
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
    ├── default/
    │   ├── settings.json
    │   ├── keybindings.json
    │   └── snippets/
    └── profiles/
        └── profile-<n>/
            ├── settings.json
            ├── tasks.json
            └── mcp.json
```

同目录还会生成 `dev-forge-1.95.3-win32-x64.zip`。成功后，中间文件夹默认被保留；
使用 `--archive-only` 可只保留 ZIP。

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
  "install": {
    "mode": "merge"
  },
  "extensions": {
    "default": ["eamodio.gitlens"],
    "profiles": {
      "Java": ["redhat.java", "vscjava.vscode-maven"],
      "Python": ["ms-python.python", "charliermarsh.ruff"]
    }
  },
  "settings": {
    "default": "./vscode-settings/shared.json",
    "profiles": {
      "Java": {"use_default": true},
      "Python": {"use_default": true}
    }
  },
  "resources": {
    "default": {
      "keybindings": "./vscode-profile/keybindings.json",
      "snippets": "./vscode-profile/snippets"
    },
    "profiles": {
      "Java": {
        "keybindings": {"use_default": true},
        "snippets": {"use_default": true},
        "tasks": "./vscode-profile/java-tasks.json"
      },
      "Python": {
        "keybindings": {"use_default": true},
        "snippets": {"use_default": true},
        "mcp": "./vscode-profile/python-mcp.json"
      }
    }
  },
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
| `install` | 对象 | 否 | `{"mode":"merge"}` | 离线安装行为 |
| `install.mode` | 字符串 | 否 | `merge` | `merge`、`replace` |
| `extensions` | 数组或对象 | 否 | `[]` | 旧式 ID 数组，或包含 `default`、`profiles` 的对象 |
| `settings` | 字符串或对象 | 否 | `auto` | Default 路径及 Profile 共享/独立设置 |
| `resources` | 对象 | 否 | `{}` | keybindings、snippets、tasks、MCP 资源 |
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

### `install`

- 类型：对象。
- 必填：否。
- 默认值：`{"mode":"merge"}`。
- `mode` 只允许 `merge` 或 `replace`。

两种模式只控制扩展清理策略，不会改变 settings 和其他 Profile 资源的覆盖开关：

- `merge`：保留本机已有扩展；各 Profile 中 ID 和版本均与离线清单一致的扩展会跳过，
  缺失或版本不同的扩展才会安装。
- `replace`：安装 VS Code 前删除当前用户扩展目录和各 Profile 的 `extensions.json`，然后
  完全按照离线清单重建扩展集合。未列入清单的本地扩展会被删除。

生成的 `install.ps1` 使用配置值作为默认模式，也可以在安装时临时覆盖：

```powershell
.\install.ps1 -Mode merge
.\install.ps1 -Mode replace
```

为了兼容旧配置，省略 `install` 时使用更安全的 `merge`。

### `extensions`

- 类型：对象；为了兼容旧配置，也接受字符串数组。
- 必填：否。
- 默认值：空数组 `[]`，即不下载扩展。
- `default`：通用扩展数组，安装到 Default，并同步安装到每个已配置 Profile。
- `profiles`：以任意 Profile 名称为键、扩展数组为值的对象。
- Profile 名称去除首尾空格后不能为空，且不能使用保留名称 `Default`。
- 每项格式：Marketplace 扩展 ID `publisher.name`。
- ID 不区分大小写，读取后会转成小写；同一分组内重复 ID 会自动去重。
- Profile 中重复声明的 `default` 扩展会自动忽略，因为它已经会同步到该 Profile。
- 同一个扩展出现在多个 Profile 时只下载一次，但会登记到每个对应 Profile。

例如：

```json
"extensions": {
  "default": [
    "eamodio.gitlens",
    "editorconfig.editorconfig"
  ],
  "profiles": {
    "Java": [
      "redhat.java",
      "vscjava.vscode-maven"
    ],
    "Python": [
      "ms-python.python",
      "charliermarsh.ruff"
    ],
    "Data Science": [
      "ms-toolsai.jupyter"
    ]
  }
}
```

上例会创建 `Java`、`Python`、`Data Science` 三个 Profile。GitLens 和 EditorConfig
会同时用于 Default 及这三个 Profile；其他扩展只进入声明它们的 Profile。

旧格式继续有效，其中所有扩展均视为通用扩展：

```json
"extensions": [
  "ms-python.python",
  "dbaeumer.vscode-eslint"
]
```

#### 最合适版本的选择规则

Dev Forge 不会直接下载 Marketplace 中版本号最大的文件。它先排除不适合目标环境的版本，
再从剩余候选中选择最高稳定版本。筛选所使用的目标环境来自 `vscode.version` 和
`vscode.arch`。

对于列表中的每个扩展 ID，程序按照以下顺序处理：

1. 查询 Marketplace 中该扩展的所有版本、版本属性、目标平台和 VSIX 地址；
2. 排除标记为 Pre-release 的预发布版本；
3. 读取 `Microsoft.VisualStudio.Code.Engine`，按 `engines.vscode` 约束检查目标 VS Code 版本；
4. 排除 Linux、macOS 和其他 Windows 架构的专用构建；
5. 在符合条件的候选项中，首先按语义化版本号选择最高版本；
6. 同一个版本存在多个构建时，优先选择目标 Windows 架构的专用 VSIX，其次选择通用 VSIX；
7. 使用 Marketplace 返回的 `Microsoft.VisualStudio.Services.VSIXPackage` 地址下载文件。

选择优先级可以概括为：

```text
稳定版本且兼容目标 VS Code
    ↓
兼容目标 Windows 架构或属于通用构建
    ↓
选择版本号最高的候选项
    ↓
同版本时：平台专用 VSIX 优先于通用 VSIX
```

例如目标环境是 VS Code `1.132.0` 和 Windows x64：

| 插件版本 | `engines.vscode` | 目标平台 | 处理结果 |
| --- | --- | --- | --- |
| `4.0.0` | `^1.133.0` | `universal` | VS Code 版本不兼容，排除 |
| `3.6.0` | `^1.100.0` | `linux-x64` | 平台不兼容，排除 |
| `3.5.0` | `^1.100.0` | `universal` | 保留为候选 |
| `3.5.0` | `^1.100.0` | `win32-x64` | 同版本中优先选择 |
| `3.4.0` | `^1.90.0` | `win32-x64` | 兼容，但版本较低 |

如果较新的版本是通用 VSIX，而较旧版本是 Windows 专用 VSIX，程序会选择较新的通用版本。
平台专用优先级只用于比较同一个插件版本的不同构建。

版本兼容检查支持扩展清单中常用的 npm 风格约束：

| 约束形式 | 示例 |
| --- | --- |
| 精确版本 | `1.90.0` |
| 比较运算 | `>=1.90.0`、`>=1.90.0 <2.0.0` |
| Caret | `^1.90.0` |
| Tilde | `~1.90.0` |
| 通配符 | `1.90.x`、`1.x`、`*` |
| 连字符范围 | `1.90.0 - 1.100.0` |
| OR 条件 | `^1.90.0 \|\| ^1.100.0` |

无法识别的版本约束会按“不兼容”处理，不会冒险下载可能无法安装的版本。
如果没有任何稳定、兼容的平台构建，打包会停止并指出具体扩展 ID、目标 VS Code
版本和目标平台。

下载后的文件名包含扩展 ID、实际版本和可选的平台标记，例如：

```text
extensions/dbaeumer.vscode-eslint-3.0.34.vsix
extensions/ms-python.python-2026.4.0-win32-x64.vsix
```

每个扩展的 ID、实际版本、`engines.vscode`、目标平台、来源 URL 和 SHA-256 都会写入
`manifest.json`，便于审计和校验。

当前限制：

- 不支持在配置中固定某个扩展版本；
- 不下载预发布版本；
- 只下载 `extensions` 中显式列出的 ID；
- 不自动展开 Extension Pack；
- 不递归下载 `extensionDependencies` 或 `extensionPack` 成员。

生成的 `install.ps1` 完全按照 `extensions.default` 和 `extensions.profiles` 生成，
不包含写死的语言或扩展 ID。由于 VS Code Profile 的扩展集合互相独立，通用扩展除了
安装到 Default，也会同时登记到所有已配置 Profile。

安装扩展前，脚本会通过 `Ensure-Profile` 显式检查并创建每个 Profile。由于 VS Code CLI
没有独立的无界面 `create-profile` 命令，脚本会使用目标 Profile 打开一个临时空文件夹，
轮询到 CLI 能识别后关闭该启动窗口，再继续安装扩展。这条路径使用 VS Code 自身的 Profile
创建流程，不再手工构造内部 ID 或 Profile 元数据。
创建和插件安装命令不会传入 `--user-data-dir`，确保它们使用与用户正常启动 VS Code
完全相同的默认用户数据目录，安装后的 Profile 会直接出现在 Profiles 菜单中。

完全离线使用时，应将所需依赖也显式加入 `extensions` 配置。生成的安装命令会传入
`--do-not-include-pack-dependencies`，防止批量参数和 VS Code 自动展开任务同时安装同一个
扩展，引发扩展目录重命名冲突。

可用下面的命令获取本机已安装扩展的 ID：

```bash
code --list-extensions
```

### `settings`

- 类型：字符串或对象。
- 必填：否。
- 默认值：`"auto"`。
- 字符串旧格式只配置 Default：精确值 `"auto"`，或一个现有文件的路径。
- 对象格式包含 `default` 和 `profiles`。
- `auto` 区分大小写；没有找到配置文件时自动打包内容为 `{}` 的空文件。

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

显式指定的源文件必须存在并且是普通文件，否则打包会终止。程序原样复制内容，不会解析、
合并或修改 JSON/JSONC。

多个 Profile 真正共享同一份 Default 配置：

```json
"settings": {
  "default": "./vscode-settings/shared.json",
  "profiles": {
    "Java": {"use_default": true},
    "Python": {"use_default": true}
  }
}
```

安装脚本会将 Java、Python 的 `useDefaultFlags.settings` 设置为 `true`。它们切换后直接
读取目标机器的 `%APPDATA%\Code\User\settings.json`，不会复制各自的配置文件。修改
Default 设置后，共享 Profile 会立即使用新值。

更新 Profile 元数据时使用 .NET `UTF8Encoding(false)` 将 `storage.json` 写成无 BOM
UTF-8。不能使用 Windows PowerShell 5 的 `Set-Content -Encoding UTF8`，否则写入的 BOM
可能导致 VS Code 无法解析 Profile 列表，启动后只显示 Default。

Profile 也可以使用独立文件或从本机同名 Profile 自动抽取：

```json
"settings": {
  "default": "auto",
  "profiles": {
    "Java": "./vscode-settings/java.json",
    "Python": "auto"
  }
}
```

Profile 名称必须已经在 `extensions.profiles` 中声明。`"auto"` 会读取 VS Code 的
`globalStorage/storage.json`，把 Profile 名称映射到内部目录 ID；本地 Profile 或其
`settings.json` 不存在时生成空配置。

### `resources`

`resources` 用于管理 settings 之外的 Profile 资源，支持以下固定名称：

| 资源名 | 来源类型 | Default/独立 Profile 目标 |
| --- | --- | --- |
| `keybindings` | JSON/JSONC 文件 | `keybindings.json` |
| `snippets` | 目录 | `snippets/` |
| `tasks` | JSON/JSONC 文件 | `tasks.json` |
| `mcp` | JSON/JSONC 文件 | `mcp.json` |

完整示例：

```json
"resources": {
  "default": {
    "keybindings": "auto",
    "snippets": "./vscode-profile/snippets",
    "tasks": "./vscode-profile/tasks.json",
    "mcp": "./vscode-profile/mcp.json"
  },
  "profiles": {
    "Java": {
      "keybindings": {"use_default": true},
      "snippets": {"use_default": true},
      "tasks": "./vscode-profile/java-tasks.json",
      "mcp": {"use_default": true}
    }
  }
}
```

规则如下：

- `default` 和 `profiles` 都可以省略；未配置的资源不会被打包，也不会修改目标机器。
- 文件资源可以使用 `"auto"` 或文件路径；`snippets` 可以使用 `"auto"` 或目录路径。
- Default 的 `"auto"` 从本机 Default Profile 读取；Profile 的 `"auto"` 从本机同名
  Profile 读取。自动来源不存在时跳过该资源，不生成空文件。
- 显式路径不存在或类型不符时停止打包。
- `{"use_default": true}` 会设置对应的 `useDefaultFlags`，让 Profile 直接使用 Default
  资源，不复制第二份文件。
- `resources.profiles` 的名称必须已在 `extensions.profiles` 中声明。
- snippets 会递归打包，manifest 为每个文件记录 SHA-256。

安装时默认不覆盖已有文件；snippets 会保留已有文件并复制缺失文件。使用
`.\install.ps1 -ForceResources` 可覆盖 keybindings、tasks、MCP 和同名 snippet 文件。
该参数不控制 `settings.json`；settings 仍由 `-ForceSettings` 单独控制。

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

为避免覆盖已有文件，如果同名文件夹或 ZIP 已经存在，程序会停止并报错。配置中暂不
支持自定义产物名称。

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
| `--settings` | 是 | 配置中的值 | 覆盖 Default 设置路径；保留对象中的 Profile 设置 |
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

安装参数：

```powershell
# 保留现有扩展，只合并离线清单；这是默认值
.\install.ps1 -Mode merge

# 清空本地扩展后严格按清单重建
.\install.ps1 -Mode replace

# 同时覆盖 settings 和其他 Profile 资源
.\install.ps1 -Mode replace -ForceSettings -ForceResources

# 即使检测到相同版本，也强制重新运行 VS Code 安装器
.\install.ps1 -ForceVSCodeInstall
```

运行前必须关闭所有 VS Code 窗口，并从独立 PowerShell 执行。VS Code 运行中的进程会缓存
Profile 元数据，导致刚写入 `storage.json` 的 Profile 无法被 `code.cmd` 识别；安装脚本会
检测 `Code` 进程并给出明确错误。

脚本会先检查 VS Code 安装包、清单中的所有 VSIX、settings 和 Profile 资源是否存在，并
确认没有正在运行的 `Code` 进程。`merge` 模式直接保留现有扩展；`replace` 模式才会递归
删除当前用户的 `%USERPROFILE%\.vscode\extensions`、`VSCODE_EXTENSIONS` 环境变量指定的
目录，以及 ZIP Archive Mode 已存在的 `data\extensions`，再按清单重建。

对于 Installer Mode，脚本会读取现有 `code.cmd --version`。版本和架构均符合清单时默认
跳过 VS Code 安装器；需要修复安装时可使用 `-ForceVSCodeInstall`。Archive Mode 仍在目标
目录不存在时解压，目录已经存在时继续复用。

在 `replace` 模式下，删除物理扩展目录时，脚本也会删除 `%APPDATA%\Code\User\profiles` 下各 Profile 的
`extensions.json` 扩展清单，防止清单继续引用已经不存在的扩展目录。Profile 本身以及
settings、keybindings、snippets、tasks 等其他配置不会被删除。

如果启用了 Settings Sync，应先关闭 Extensions 和 Profiles 同步，避免联网后恢复旧扩展。
通过快捷方式的 `--extensions-dir` 参数指定、且未同时设置 `VSCODE_EXTENSIONS` 的其他目录
无法由脚本自动发现，需要单独清理。WSL、Remote SSH 和 Dev Container 中的远程扩展也不在
本脚本的清理范围内。

扩展以每批最多 20 个 VSIX 的方式安装，同一 Profile 不再为每个扩展单独启动一次
`code.cmd`。在 `merge` 模式下，脚本还会先读取每个 Profile 的 `ID@版本` 并跳过完全一致的
扩展。安装命令禁止自动展开 Extension Pack 和依赖，因此离线所需成员必须在配置中显式
列出。同一个 VSIX 配置到多个 Profile 时，脚本仍会在必要时关闭安装过程中残留的 `Code`
进程；如果批量安装失败，会拆分为单个扩展并自动重试，以保留具体失败扩展的错误信息。
对于共享设置的 Profile，脚本通过 `%APPDATA%\Code\User\globalStorage\storage.json`
设置 `useDefaultFlags.settings=true`；独立设置则写入对应 Profile ID 目录。使用
`.\install.ps1 -ForceSettings` 可以覆盖已有配置文件，默认不会静默覆盖。

keybindings、snippets、tasks、MCP 使用相同的 Profile 元数据机制。独立资源写入对应
Profile ID 目录；共享资源设置相应的 `useDefaultFlags`。它们由 `-ForceResources` 控制，
不会因为选择 `replace` 就自动覆盖。

## 开发与测试

```bash
python3 -m unittest discover -s tests -v
```

Marketplace 下载接口不需要令牌。网络请求会重试，下载先写入 `.part` 文件，完成后再
原子替换。
