<div align="right">

**🌐 Document Language:** **English** | [简体中文](README.md)

</div>

# CTF Platform — Cybersecurity Learning System (Personal Edition) — SNOWCTF (SCTF)

<div align="center">

**A Cybersecurity Learning & Competition Platform Designed for Chinese Users**

[![Community](https://img.shields.io/badge/QQ_Group-517929458-blue)](https://qm.qq.com/cgi-bin/qm/qr?k=xxx)
[![Official Site](https://img.shields.io/badge/Official_Site-www.secsnow.cn-green)](https://www.secsnow.cn)
[![Documentation](https://img.shields.io/badge/Documentation-View_Docs-orange)](https://www.secsnow.cn/wiki/subject/article/this-one/)

</div>

---

## Overview

The Cybersecurity Learning System is an integrated learning platform built for cybersecurity education and competitions. It combines CTF competitions, knowledge quizzes, vulnerability labs, and knowledge base management into a single system, covering competition organization, hands-on training, knowledge retention, resource management, and talent development.

**Mission**: To create and share a high-quality cybersecurity learning environment.

## Core Features

### Competition System
- **CTF Competition Platform**: Supports individual and team contests, dynamic scoring and FLAGs, situation-awareness dashboards, dual competition page themes (Simple / Tech), multi-node challenge server isolation, scaling and monitoring, flexible competition permissions. Member users can create and manage their own competitions from the frontend, and integrate with knowledge competitions.
- **Knowledge Competition System**: Theory-based Q&A, integrated with the CTF system. Supports single-choice, multiple-choice, and true/false questions, automatic grading, registration mode for knowledge contests, pass/fail threshold mode, and configurable answer visibility.
- **Lab Practice System**: Member-based practice challenges; members can create challenges from the frontend. “Learning Island” style practice by category; full dynamic Markdown documentation; comments and discussion.

### Competition Frontend Themes

- **Anime Theme**: Anime-style competition pages.
- **Tech Theme**: Tech-style competition pages.

## Personal Edition Documentation & Support

- **Online Demo**: [www.secsnow.cn](https://www.secsnow.cn/)
- **Documentation**: [Official Docs](https://www.secsnow.cn/wiki/subject/article/this-one/)
- **Community QQ Group**: 517929458

## Cybersecurity Learning System — Professional Edition

The Professional Edition targets **enterprises, universities, and commercial use**. It builds on all Personal Edition features with a FASTAPI-based backend (performance comparable to Go, per official description) for higher concurrency and to address performance limits of the Personal Edition in some scenarios. The Professional Edition removes non-essential features and focuses on core learning and competition capabilities, with additional enterprise and scale-oriented features.

### Personal Edition vs Professional Edition

| | Personal Edition | Professional Edition |
|--|------------------|------------------------|
| **Use Cases** | Personal learning, small teams, typical campus contests and training | Enterprise / university, commercial deployment, medium- to large-scale competitions and training |
| **Performance** | 1x TPS | 5x TPS |
| **Core Features** | CTF, vulnerability labs, knowledge competitions | CTF, vulnerability labs, knowledge competitions, attack-defense competitions (not yet officially released) |
| **Feature Differences** | Optional competition themes, non-core features | Multi-FLAG scenarios, dynamic/static scoring and flags, full admin backend, compliance (e.g. level protection), security audit, access control, container proxy and access limits, AWD/AWDP support (not yet released), integrated theory + practice competition mode, enhanced anti-cheating and auditing |
| **Demo** | None | Client: [http://111.228.44.199:8282/](http://111.228.44.199:8282/), Admin: [http://111.228.44.199:8787/](http://111.228.44.199:8787/), Login: **ctfer** / **ctf@5678** |

> For Professional Edition deployment and partnership, contact: **secsnowteam@gmail.com**

## Personal Edition — Anime Theme Preview

#### Track Selection

![Main Page](docs/images/图3.jpg)

#### Competition List

![Competition](docs/images/图4.jpg)

#### Data Dashboard
![Competition](docs/images/图2.jpg)

#### Challenge Page
![Competition](docs/images/图1.jpg)

## Personal Edition — Tech Theme Preview

#### Data Dashboard
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/数据大屏.png)

#### Solve Feed
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/解题动态.png)

#### Challenge Page
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/比赛答题页面.png)

#### Answer Page
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/容器下方打码.png)

## Shared Pages

#### Competition List

![Main Page](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/比赛列表.png)

#### Auto Registration
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/报名系统.png)

#### Frontend Competition Management
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/前台比赛管理.png)

#### Registration Info
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/报名信息.png)

#### Create Challenge
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/前台创建题目.png)

#### Profile Page
![Competition](https://cdn.jsdelivr.net/gh/TheMoonu/TheMoonu/个人信息页面.png)

---

## Contributing & Feedback

Issues and Pull Requests are welcome. Your support drives this open-source project.

For questions or suggestions, join the community QQ group: **517929458**

## Acknowledgments

This project uses code and architecture from the following open-source projects:
- [izone](https://github.com/Hopetree/izone) — Blog system architecture reference
- [simpleui](https://github.com/newpanjing/simpleui) — Django admin UI framework

## License & Disclaimer

### Software License

See the [LICENSE](LICENSE) file.

### Third-Party Components

This project uses several open-source components, each under its own license.  
For a list and license details, see [THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES).

**Important**:
- Please read LICENSE and THIRD-PARTY-NOTICES before using this software.
- Third-party components retain their original licenses.
- This open-source project is the **Personal Edition** of the Cybersecurity Learning System. **The Personal Edition may not be used for any commercial purpose.**

---

<div align="center">

**If this project helps you, please give us a Star!**

Made with ❤️ by Cybersecurity Learning System Team

</div>
