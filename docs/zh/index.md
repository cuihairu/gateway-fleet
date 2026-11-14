---
layout: home
hero:
  name: gateway-fleet
  text: LLM 网关舰队的控制面配置
  tagline: 非密配置在 fleet.yaml；每一条密钥都留在 persona 保险库里。
  actions:
    - theme: brand
      text: 总览
      link: /zh/overview
    - theme: alt
      text: GitHub
      link: https://github.com/cuihairu/gateway-fleet
features:
  - icon: 🔐
    title: 密钥永不落地
    details: fleet.yaml 只放 SecretRef。取值经 persona Connect API（unix socket + 作用域 pconn token）解析——不经主密码，本仓库不含任何密钥值。
  - icon: 🛡️
    title: 写入安全
    details: --apply 先在材料旁路文件上取锁，把旧内容快照到 <材料>.fleet-bak，再原子替换。清单为空时拒绝写入，绝不抹空托管块。
  - icon: 📖
    title: 只读校验
    details: bifrost 键、magpie caller 键、hermes 的 ${…} 引用，以及 litellm / hermes 配置的明文扫描——输出只有名称与结论。
---