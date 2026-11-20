# 外部依赖与来源

本项目由 embuddies 工作台精简演进，保留其桌面宿主、模型配置及存储基础：
https://github.com/RiseBun/embuddies-agent

真实仿真后端依赖 CaP-X：
https://github.com/capgym/cap-x

服务器实际使用的 CaP-X 提交为 `53e9966d7a8e2fa7494676772bccc35280f5c0ed`，上游工作区无本地改动。本仓库的修改位于 cap-runtime，而不是直接修改上游。按照上游文档安装其环境、SAM3、Contact GraspNet、Pyroki、Robosuite 和 Panda 描述依赖；模型权重应从各自授权来源获取。

可以使用以下布局，对齐附带的服务启动器：

```bash
git clone https://github.com/capgym/cap-x third_party/cap-x
git -C third_party/cap-x checkout 53e9966d7a8e2fa7494676772bccc35280f5c0ed
# 接着按照该提交的上游安装说明配置 Python 环境及模型。
```

`cap-runtime/scripts/capx_services.py` 来自本项目服务器的实际启动器，默认以当前仓库为 CAP_PROJECT_ROOT，也可以显式设置该环境变量。SAM3 本地权重位置为 models/sam3/sam3.pt；脚本会核对实际使用权重的 SHA256，架构或权重不匹配会拒绝启动。其余权重路径及额外模型依赖按 CaP-X 配置。不要把这些权重提交到本仓库。

桌面宿主使用锁定的 Electron，前端使用 React、Vite、TypeScript。npm 锁文件随源码提供；各第三方组件的许可仍以其上游声明为准。本仓库不重新分发其依赖安装目录或模型权重。
