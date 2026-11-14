// SPDX-License-Identifier: Apache-2.0
import { defineConfig } from 'vitepress'

// Docs site for gateway-fleet. Root locale is English, `zh` is Simplified
// Chinese. Language switching is manual only (langMenuLabel in both locales);
// no browser-language auto-redirect is wired up — see the todo list in the
// audit page. Content stays in sync with README.md / README.zh.md.
const base = process.env.DOCS_BASE || '/gateway-fleet/'

export default defineConfig({
  title: 'gateway-fleet',
  description:
    'Control-plane config for an LLM gateway fleet: non-secret config in fleet.yaml, secrets in the persona vault.',
  base,
  cleanUrls: false,
  lastUpdated: true,
  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
      link: '/',
      themeConfig: {
        langMenuLabel: 'Language',
        nav: [
          { text: 'Overview', link: '/overview' },
          { text: 'Features', link: '/features' },
          { text: 'Config keys', link: '/keys' },
          { text: 'Usage', link: '/usage' },
        ],
        outline: { label: 'On this page' },
        docFooter: { prev: 'Previous page', next: 'Next page' },
        lastUpdated: { text: 'Last updated' },
        search: { provider: 'local' },
        socialLinks: [{ icon: 'github', link: 'https://github.com/cuihairu/gateway-fleet' }],
        footer: { message: 'Secrets stay in the persona vault — this repo never holds key values.' }
      }
    },
    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh/',
      description: 'LLM 网关舰队的控制面配置：非密配置在 fleet.yaml，密钥在 persona 保险库。',
      themeConfig: {
        langMenuLabel: '语言',
        nav: [
          { text: '总览', link: '/zh/overview' },
          { text: '功能清单', link: '/zh/features' },
          { text: '配置键', link: '/zh/keys' },
          { text: '用法', link: '/zh/usage' },
        ],
        outline: { label: '本页目录' },
        docFooter: { prev: '上一页', next: '下一页' },
        lastUpdated: { text: '最后更新' },
        returnToTopLabel: '返回顶部',
        sidebarMenuLabel: '菜单',
        darkModeSwitchLabel: '外观',
        search: { provider: 'local' },
        socialLinks: [{ icon: 'github', link: 'https://github.com/cuihairu/gateway-fleet' }],
        footer: { message: '密钥只在 persona 保险库——本仓库不含任何密钥值。' }
      }
    }
  },
  themeConfig: {
    search: { provider: 'local' },
    sidebar: {
      '/zh/': [
        {
          text: 'gateway-fleet',
          items: [
            { text: '总览', link: '/zh/' },
            { text: '功能清单', link: '/zh/features' },
            { text: '配置键与参数', link: '/zh/keys' },
            { text: '用法与轮换', link: '/zh/usage' },
            { text: '已知缺口', link: '/zh/gaps' },
            { text: '文档一致性审计', link: '/zh/audit' }
          ]
        }
      ],
      '/': [
        {
          text: 'gateway-fleet',
          items: [
            { text: 'Overview', link: '/' },
            { text: 'Features', link: '/features' },
            { text: 'Config keys and flags', link: '/keys' },
            { text: 'Usage and rotation', link: '/usage' },
            { text: 'Known gaps', link: '/gaps' },
            { text: 'Docs consistency audit', link: '/audit' }
          ]
        }
      ]
    }
  }
})