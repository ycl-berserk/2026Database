"""生成可复现的模拟聊天记录样例（JSON / Markdown），用于导入与验收演示。"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
CST = timezone(timedelta(hours=8))

EXAM_HINTS = {
    "DB": "先判断题目属于查询、并发还是设计：查询题先画执行顺序并检查索引；并发题写隔离级别与可能异常；设计题先列函数依赖再分解。",
    "OS": "先定位题目问的是调度、同步还是内存：调度题画甘特图；同步题写信号量初值与 P/V 顺序；内存题先画页表再算地址。",
    "NET": "先定位协议层次：链路层看帧与 MAC，网络层看地址与路由，传输层看端口、序号和窗口，再按时间线作答。",
    "ML": "先明确任务类型与评价指标，再写模型假设、损失函数和正则项；涉及泛化时用训练/验证误差曲线解释。",
    "DS": "先写数据规模与操作序列，再推复杂度；树/图题先画结构，排序题说明稳定性与最坏情况。",
}

TOPICS: dict[str, list[dict]] = {
    "DB": [
        {
            "title": "SQL 执行顺序与 JOIN",
            "tags": ["SQL", "查询执行"],
            "question": "SELECT 语句里 WHERE、GROUP BY、HAVING 到底谁先执行？",
            "answer": "逻辑执行顺序是 FROM → WHERE → GROUP BY → HAVING → SELECT → ORDER BY → LIMIT。JOIN 属于 FROM 阶段，先形成中间结果，再由 WHERE 过滤行、GROUP BY 聚合、HAVING 过滤组。",
            "example": "统计每门课的消息数并筛出大于 50 的组：先连接会话与消息，再按课程分组聚合，最后用 HAVING 过滤。",
            "pitfall": "把聚合条件写进 WHERE。聚合函数只能出现在 HAVING 或 SELECT 中，WHERE 阶段还没有分组。",
            "one_line": "先 FROM 后 WHERE，先 GROUP 再 HAVING，SELECT 只是投影。",
            "related": "窗口函数与 GROUP BY 的区别",
            "real": "报表页的“课程消息量 TopN”就是这一执行顺序的典型应用。",
        },
        {
            "title": "索引与 B+ 树",
            "tags": ["索引", "B+树"],
            "question": "为什么给消息内容建索引后，LIKE 查询还是可能很慢？",
            "answer": "普通 B+ 树适合等值匹配和前缀匹配，`%关键词%` 这种两侧通配无法利用前缀定位。中文子串检索可以改用 pg_trgm 的 GIN 索引，或先用课程、日期等条件缩小范围。",
            "example": "在 messages(content) 上建立 gin_trgm_ops 索引后，ILIKE '%事务%' 可以走索引扫描。",
            "pitfall": "以为索引越多越好。写操作多的表上堆叠索引会拖慢插入，并占用大量磁盘。",
            "one_line": "索引匹配的是访问模式，而不是某一列本身。",
            "related": "复合索引的最左前缀原则",
            "real": "学习空间的消息检索就使用 trigram 索引支持中文子串查询。",
        },
        {
            "title": "事务与隔离级别",
            "tags": ["事务", "隔离级别"],
            "question": "脏读、不可重复读和幻读分别对应什么隔离级别？",
            "answer": "READ COMMITTED 防止脏读；REPEATABLE READ 防止不可重复读，PostgreSQL 在其上也避免了幻读；SERIALIZABLE 通过冲突检测提供最强保证，但可能要求应用重试。",
            "example": "导入时一个事务同时写会话、消息和标签，任何一步失败都整体回滚，成功才一起提交。",
            "pitfall": "只在应用层写 try/except，却没有把多条 SQL 放进同一个数据库事务。",
            "one_line": "事务把一组操作变成一个原子结果。",
            "related": "MVCC 与显式锁",
            "real": "导入批次用单事务保证不会出现“有会话没消息”的半成品。",
        },
        {
            "title": "范式与函数依赖",
            "tags": ["范式", "函数依赖"],
            "question": "第三范式到底在消除什么依赖？",
            "answer": "3NF 消除非主属性对候选键的传递依赖。例如在消息表里重复存课程名，课程名会经会话间接依赖消息，产生更新异常，应把课程拆到独立表并通过外键关联。",
            "example": "会话只保存 course_id，课程名称放在 courses 表；修改课程名时只改一行。",
            "pitfall": "把“少写一个 JOIN”当成冗余的理由，忽略插入、更新和删除异常。",
            "one_line": "一份事实只存一处，通过键建立关联。",
            "related": "BCNF 与多值依赖",
            "real": "本系统的会话表不冗余来源所有者，而是通过 source 关联取得。",
        },
        {
            "title": "视图与权限",
            "tags": ["视图", "权限"],
            "question": "为什么 Text2SQL 不直接查业务表，而是查视图？",
            "answer": "视图可以固定行级过滤条件与列集合，再把视图权限授予只读角色，数据库层就能兜底越权。即使大模型生成了跨用户的 SQL，也只能看到视图中属于当前用户的行。",
            "example": "v_my_conversations 用 current_setting('app.user_id') 过滤当前用户可访问的会话。",
            "pitfall": "只靠提示词约束大模型，数据库仍然暴露全表权限。",
            "one_line": "权限应实现在数据库边界，而不是只写在应用代码里。",
            "related": "security_barrier 与 security_invoker",
            "real": "本系统的 t2s schema 就是这一设计：只读角色只能查询视图。",
        },
        {
            "title": "并发控制与锁",
            "tags": ["并发", "锁"],
            "question": "两个请求同时导入同一个文件，会发生什么？",
            "answer": "唯一键可以防重复会话与消息，但多条语句仍可能交错。可以用 pg_advisory_xact_lock 按来源串行化导入事务，事务结束自动释放锁。",
            "example": "同一来源的导入先取事务级 advisory lock，再执行 upsert 与消息插入。",
            "pitfall": "用进程内内存锁，多进程部署后完全失效。",
            "one_line": "唯一约束加事务锁，共同保证导入幂等。",
            "related": "乐观锁与悲观锁",
            "real": "导入服务在事务内按 source_id 获取 advisory lock。",
        },
    ],
    "OS": [
        {
            "title": "进程与线程",
            "tags": ["进程", "线程"],
            "question": "进程和线程的本质区别是什么？",
            "answer": "进程是资源分配单位，拥有独立地址空间；线程是调度单位，共享所属进程的资源。线程切换更轻，但共享内存会带来同步问题。",
            "example": "浏览器用多进程隔离标签页，标签页内部再用多线程处理渲染与网络。",
            "pitfall": "认为线程一定更快。线程共享地址空间，错误会互相影响，CPU 密集任务还受核数限制。",
            "one_line": "进程隔离资源，线程共享资源。",
            "related": "协程与用户态调度",
            "real": "Web 服务用连接池和并发模型处理大量请求。",
        },
        {
            "title": "进程调度",
            "tags": ["调度", "FCFS"],
            "question": "先来先服务、短作业优先和时间片轮转各适合什么场景？",
            "answer": "FCFS 简单但长作业会拖累短作业；SJF 平均等待时间短，但需要预知运行时间且可能饿死长作业；RR 用时间片保证响应性，适合交互系统。",
            "example": "给定三个作业的到达与运行时间，画甘特图并计算平均周转时间。",
            "pitfall": "把响应时间和周转时间混为一谈。响应时间是从提交到首次运行，周转时间是从提交到完成。",
            "one_line": "不同调度目标对应不同指标与策略。",
            "related": "优先级反转",
            "real": "操作系统实验里常用 RR 模拟交互式终端。",
        },
        {
            "title": "死锁",
            "tags": ["死锁", "同步"],
            "question": "死锁的四个必要条件是什么，怎么破坏？",
            "answer": "互斥、占有并等待、不可抢占、循环等待。常见做法是固定加锁顺序破坏循环等待，或一次性申请全部资源。",
            "example": "两个事务都按 id 从小到大加锁，就不会形成交叉等待。",
            "pitfall": "只加超时却不重试，或检测到死锁后没有回滚事务。",
            "one_line": "破坏任一必要条件即可预防死锁。",
            "related": "银行家算法",
            "real": "PostgreSQL 会检测死锁并回滚其中一个事务。",
        },
        {
            "title": "虚拟内存与分页",
            "tags": ["虚拟内存", "分页"],
            "question": "虚拟地址是怎么翻译成物理地址的？",
            "answer": "CPU 发出虚拟地址，按页大小拆成页号和页内偏移；页表把页号映射到物理页框，TLB 缓存最近的映射以加速。",
            "example": "4KB 页、二级页表下，先用页目录号查目录项，再用页表索引查页表项。",
            "pitfall": "忘记页内偏移不参与页表查找，它直接拼到物理页框地址后面。",
            "one_line": "页号查表，偏移不变。",
            "related": "多级页表与倒排页表",
            "real": "进程隔离依赖每个进程独立的页表。",
        },
        {
            "title": "页面置换",
            "tags": ["页面置换", "LRU"],
            "question": "FIFO、LRU 和 Clock 算法有什么区别？",
            "answer": "FIFO 按进入内存的顺序淘汰，可能出现 Belady 异常；LRU 淘汰最久未使用的页，效果好但维护成本高；Clock 用访问位近似 LRU，开销更低。",
            "example": "给定引用串，分别模拟三种算法并统计缺页次数。",
            "pitfall": "把 LRU 和 LFU 混淆。LFU 看访问频率，LRU 看最近一次访问时间。",
            "one_line": "置换算法在缺页率与实现成本之间取舍。",
            "related": "工作集模型",
            "real": "数据库缓冲池也使用类似 LRU 的淘汰策略。",
        },
        {
            "title": "文件系统",
            "tags": ["文件系统", "inode"],
            "question": "inode 里存了什么，文件名又在哪里？",
            "answer": "inode 存权限、大小、时间戳和数据块指针；目录项把文件名映射到 inode 号。硬链接就是多个目录项指向同一个 inode。",
            "example": "删除文件实际是减少 inode 的链接计数，计数为 0 且无进程打开时才释放数据块。",
            "pitfall": "认为文件名存在 inode 内。文件名属于目录项，同一 inode 可以有多个名字。",
            "one_line": "目录管名字，inode 管数据。",
            "related": "软链接与硬链接",
            "real": "上传文件落盘与数据库记录分离，需要考虑一致性。",
        },
    ],
    "NET": [
        {
            "title": "TCP 三次握手与四次挥手",
            "tags": ["TCP", "握手"],
            "question": "为什么建立连接要三次握手，关闭却要四次挥手？",
            "answer": "三次握手让双方确认收发能力并同步序号；关闭时一方 FIN 只表示自己不再发送，另一方可能还有数据，因此 ACK 与 FIN 通常分开发送。",
            "example": "客户端 FIN → 服务端 ACK → 服务端处理完数据发 FIN → 客户端 ACK，进入 TIME_WAIT。",
            "pitfall": "把 TIME_WAIT 当成异常状态强行清理。它保证最后的 ACK 能重传，并让旧报文失效。",
            "one_line": "握手确认双向可达，挥手等待双向结束。",
            "related": "SYN Flood 与半连接队列",
            "real": "长连接复用可以显著减少握手开销。",
        },
        {
            "title": "TCP 拥塞控制",
            "tags": ["TCP", "拥塞控制"],
            "question": "慢启动、拥塞避免、快重传和快恢复怎么配合？",
            "answer": "慢启动指数增大拥塞窗口，到达阈值后线性增长；收到三个重复 ACK 触发快重传与快恢复，避免直接回到慢启动。",
            "example": "cwnd 从 1 个 MSS 开始翻倍到 ssthresh，之后每个 RTT 增加一个 MSS。",
            "pitfall": "把接收窗口和拥塞窗口混为一谈。发送窗口取两者的较小值。",
            "one_line": "发送窗口取接收窗口与拥塞窗口的较小值。",
            "related": "BBR 与丢包驱动的区别",
            "real": "视频卡顿常与拥塞窗口骤降有关。",
        },
        {
            "title": "IP 与子网划分",
            "tags": ["IP", "子网"],
            "question": "给了 192.168.10.0/24，怎么划出 4 个等长子网？",
            "answer": "借 2 位主机位，掩码变为 /26，每个子网 64 个地址，网络号间隔 64：.0、.64、.128、.192。",
            "example": "192.168.10.70/26 属于 .64 子网，广播地址是 .127。",
            "pitfall": "忘记扣除网络地址与广播地址，实际可用主机数是 62 而不是 64。",
            "one_line": "借位扩子网，块大小决定边界。",
            "related": "CIDR 与路由聚合",
            "real": "容器网络也按子网划分地址池。",
        },
        {
            "title": "HTTP 与 HTTPS",
            "tags": ["HTTPS", "TLS"],
            "question": "HTTPS 到底加密了什么，证书解决什么问题？",
            "answer": "TLS 用非对称握手协商对称会话密钥，之后用对称加密传输；证书由 CA 签名，绑定域名与公钥，防止中间人冒充。",
            "example": "浏览器校验证书链与域名，验证通过后建立加密通道。",
            "pitfall": "认为 HTTPS 能隐藏访问的域名和 IP。SNI 与 DNS 仍可能暴露目标站点。",
            "one_line": "证书证明身份，对称密钥保护内容。",
            "related": "HSTS 与证书固定",
            "real": "生产环境应强制 HTTPS，并给会话 Cookie 加 Secure 属性。",
        },
        {
            "title": "DNS 解析",
            "tags": ["DNS", "缓存"],
            "question": "输入网址后，DNS 解析大致经过哪些步骤？",
            "answer": "先查本地缓存与 hosts，再询问递归解析器；解析器从根、顶级域到权威服务器逐级查询，结果按 TTL 缓存。",
            "example": "解析 www.example.com 时依次询问根、.com 和 example.com 的权威服务器。",
            "pitfall": "忽略 TTL 导致排障时仍看到旧记录。迁移期间应提前调低 TTL。",
            "one_line": "递归查询替客户端跑完整个链路。",
            "related": "CNAME 与 A 记录",
            "real": "服务迁移时用低 TTL 缩短切换时间。",
        },
        {
            "title": "路由与转发",
            "tags": ["路由", "转发"],
            "question": "路由器转发一个分组时会改哪些字段？",
            "answer": "查路由表选下一跳与出接口，重写链路层帧头，TTL 减一；源和目的 IP 通常不变，NAT 场景例外。",
            "example": "主机把包发给网关，网关查表后封装新的 MAC 帧发往下一跳。",
            "pitfall": "以为每一跳都会修改目的 IP。IP 是端到端寻址，MAC 才是逐跳替换。",
            "one_line": "IP 端到端，MAC 逐跳替换。",
            "related": "NAT 与最长前缀匹配",
            "real": "排查跨网段问题时，要同时看路由表与 ARP 缓存。",
        },
    ],
    "ML": [
        {
            "title": "过拟合与正则化",
            "tags": ["过拟合", "正则化"],
            "question": "训练集很好、验证集很差，应该怎么处理？",
            "answer": "这是过拟合的典型信号。可以增加数据、降低模型复杂度、加 L1/L2 正则、早停或使用 Dropout，同时检查是否存在数据泄漏。",
            "example": "多项式回归阶数越高训练误差越低，但验证误差先降后升。",
            "pitfall": "用测试集反复调参，导致测试集信息泄漏，最终评估偏乐观。",
            "one_line": "泛化差距来自模型复杂度与数据量不匹配。",
            "related": "偏差-方差权衡",
            "real": "任何离线评估都要严格区分训练、验证与测试数据。",
        },
        {
            "title": "交叉验证",
            "tags": ["交叉验证", "评估"],
            "question": "K 折交叉验证为什么比单次划分更可靠？",
            "answer": "它轮流用不同折做验证，降低单次划分的偶然性，并给出均值与方差；分层 K 折还能保持类别比例。",
            "example": "5 折交叉验证得到 5 个验证分数，报告均值±标准差。",
            "pitfall": "在划分前做全局标准化，造成验证折信息泄漏。预处理应放进每折的训练流程。",
            "one_line": "多次轮换评估，减少划分偶然性。",
            "related": "时间序列交叉验证",
            "real": "小数据集上尤其需要交叉验证来稳定评估。",
        },
        {
            "title": "梯度下降",
            "tags": ["梯度下降", "优化"],
            "question": "批量、随机和小批量梯度下降怎么选？",
            "answer": "批量用全量数据，方向稳但慢；随机每次一个样本，噪声大；小批量折中，能利用向量化且更新频繁。",
            "example": "学习率过大时损失震荡甚至发散，过小则收敛缓慢。",
            "pitfall": "忘记特征缩放，导致不同维度梯度尺度差异巨大，难以选学习率。",
            "one_line": "用批量大小与学习率平衡稳定性与速度。",
            "related": "动量与 Adam",
            "real": "深度学习框架默认使用小批量训练。",
        },
        {
            "title": "逻辑回归",
            "tags": ["逻辑回归", "分类"],
            "question": "逻辑回归为什么用交叉熵而不是均方误差？",
            "answer": "Sigmoid 输出概率，交叉熵对应伯努利似然，梯度形式简洁且凸性更好；均方误差配 Sigmoid 容易出现梯度消失。",
            "example": "二分类标签为 0/1，损失为 -[y log p + (1-y) log(1-p)]。",
            "pitfall": "把输出概率直接当类别，忽略阈值选择与类别不平衡。",
            "one_line": "概率模型配对数似然。",
            "related": "Softmax 与多分类交叉熵",
            "real": "点击率预估常用逻辑回归作为基线模型。",
        },
        {
            "title": "决策树与集成",
            "tags": ["决策树", "集成学习"],
            "question": "随机森林和梯度提升树的核心区别是什么？",
            "answer": "随机森林并行训练多棵深树并对结果投票或平均，主要降低方差；GBDT 串行拟合残差，主要降低偏差。",
            "example": "随机森林对行和特征采样；GBDT 每棵树学习前一棵的负梯度。",
            "pitfall": "认为集成一定不会过拟合。Boosting 在噪声大或迭代过多时同样会过拟合。",
            "one_line": "Bagging 降方差，Boosting 降偏差。",
            "related": "XGBoost 的正则化目标",
            "real": "表格数据上树集成通常是很强的基线。",
        },
        {
            "title": "评价指标",
            "tags": ["评价指标", "不平衡"],
            "question": "类别不平衡时为什么准确率会误导？",
            "answer": "多数类占比 99% 时，全预测多数类也有 99% 准确率。应看 Precision、Recall、F1、PR 曲线或 ROC-AUC，并结合业务代价选择阈值。",
            "example": "欺诈检测更关注召回，可以接受较低的精确率。",
            "pitfall": "只看 AUC 不看具体阈值下的混淆矩阵，上线后效果可能完全不同。",
            "one_line": "指标要匹配业务真正关注的东西。",
            "related": "代价敏感学习",
            "real": "统计分析要区分总量、比率与分布，避免单一口径。",
        },
    ],
    "DS": [
        {
            "title": "复杂度分析",
            "tags": ["复杂度", "算法分析"],
            "question": "怎么快速判断一段代码的时间复杂度？",
            "answer": "找随输入规模增长的循环与递归。单层循环 O(n)，两层嵌套 O(n²)，二分 O(log n)，递归用递推式或主定理分析。",
            "example": "双重循环内层从 0 到 i，总次数 n(n+1)/2，仍然是 O(n²)。",
            "pitfall": "忽略循环内的隐式拷贝或字符串拼接成本。",
            "one_line": "看增长最快的项，忽略常数与低阶项。",
            "related": "空间复杂度与均摊分析",
            "real": "数据库查询也要看执行计划，而不是只看 SQL 长短。",
        },
        {
            "title": "二叉树遍历",
            "tags": ["二叉树", "遍历"],
            "question": "前序、中序、后序和层序遍历分别怎么用？",
            "answer": "前序适合复制结构，中序在二叉搜索树上得到有序序列，后序适合释放节点或计算子树结果，层序用队列按层处理。",
            "example": "表达式树的后序遍历得到后缀表达式。",
            "pitfall": "递归遍历时把访问顺序和递归顺序搞反。",
            "one_line": "遍历顺序决定根节点在第几步被访问。",
            "related": "Morris 遍历",
            "real": "目录树遍历常用递归或显式栈。",
        },
        {
            "title": "平衡树",
            "tags": ["平衡树", "AVL"],
            "question": "AVL 树和红黑树怎么选？",
            "answer": "AVL 更严格平衡，查找更快但插入删除旋转多；红黑树平衡较松，更新代价低，适合频繁修改的场景。",
            "example": "插入导致失衡时，AVL 根据 LL/RR/LR/RL 做单旋或双旋。",
            "pitfall": "只记旋转名字，不会根据左右子树高度差判断类型。",
            "one_line": "查询多选 AVL，更新多选红黑树。",
            "related": "B+ 树与磁盘索引",
            "real": "数据库索引多用 B+ 树，因为磁盘块访问成本高。",
        },
        {
            "title": "图的遍历",
            "tags": ["图", "BFS"],
            "question": "BFS 和 DFS 分别适合解决什么问题？",
            "answer": "BFS 按层扩展，适合无权图最短路径和层级关系；DFS 适合连通性、拓扑排序和回溯搜索。",
            "example": "迷宫最短步数用 BFS；判断有向图是否有环可用 DFS 三色标记。",
            "pitfall": "忘记标记已访问，导致重复入队或死循环。",
            "one_line": "BFS 找最近，DFS 探到底。",
            "related": "拓扑排序",
            "real": "课程依赖关系可以用有向图建模。",
        },
        {
            "title": "最短路径",
            "tags": ["最短路径", "Dijkstra"],
            "question": "Dijkstra 为什么不能处理负权边？",
            "answer": "它基于贪心：节点一旦出队就认为最短距离已确定。负权边可能在之后提供更短路径，使这个假设失效；负权可用 Bellman-Ford。",
            "example": "存在负权环时最短路径不存在，Bellman-Ford 可以检测出来。",
            "pitfall": "把 Dijkstra 用在含负权的图上却不自知，结果悄悄出错。",
            "one_line": "贪心正确性依赖非负权。",
            "related": "Floyd-Warshall",
            "real": "导航系统通常使用非负权重的道路图。",
        },
        {
            "title": "排序算法",
            "tags": ["排序", "分治"],
            "question": "快排、归并和堆排序各自的特点是什么？",
            "answer": "快排平均 O(n log n)、原地但不稳定，最坏 O(n²)；归并稳定且最坏 O(n log n)，需要额外空间；堆排序原地、最坏 O(n log n)，但不稳定。",
            "example": "链表排序常选归并，因为不需要随机访问。",
            "pitfall": "只比较平均复杂度，忽略稳定性与内存开销。",
            "one_line": "复杂度、稳定性、空间三者权衡。",
            "related": "计数排序与桶排序",
            "real": "数据库 ORDER BY 可能利用索引避免显式排序。",
        },
    ],
}

FILE_SPECS = [
    ("qoder_db.json", "DB", "qoder", "alice@qoder", "json"),
    ("chatgpt_os.json", "OS", "chatgpt", "alice@chatgpt", "json"),
    ("deepseek_net.md", "NET", "deepseek", "alice@deepseek", "md"),
    ("qoder_ml.json", "ML", "qoder", "alice@qoder", "json"),
    ("chatgpt_ds.json", "DS", "chatgpt", "alice@chatgpt", "json"),
]


def build_conversation(course_code: str, topic: dict, index: int, course_index: int) -> dict:
    month = 2 + (course_index + index) % 8
    day = 4 + index * 4 + course_index
    start = datetime(2026, month, day, 19 + index % 4, (course_index * 11 + index * 7) % 60, tzinfo=CST)
    pairs = [
        (topic["question"], topic["answer"]),
        ("能结合一个具体场景解释吗？", f"可以。围绕「{topic['title']}」，一个直观的场景是：{topic['example']}"),
        ("这里最容易出错的地方是什么？", f"最常见的坑是：{topic['pitfall']}"),
        ("考试里如果换一种问法，我该怎么判断？", f"{EXAM_HINTS[course_code]} 当题干关键词落在「{topic['title']}」上时，先写出对象、约束和目标，再套方法。"),
        ("用一句话帮我记住它。", f"一句话：{topic['one_line']}"),
        ("它和哪个知识点最容易混淆？", f"最容易混淆的是「{topic['related']}」。区分时先问：讨论的是存储结构、执行过程还是评价指标？"),
        ("如果放到真实项目里，这个知识点的价值在哪？", topic["real"]),
    ]
    messages = []
    cursor = start
    for qi, (question, answer) in enumerate(pairs):
        messages.append({"role": "user", "content": question, "created_at": cursor.isoformat()})
        cursor += timedelta(minutes=2 + qi % 3)
        messages.append({"role": "assistant", "content": answer, "created_at": cursor.isoformat()})
        cursor += timedelta(minutes=1)
    return {
        "external_id": f"{course_code.lower()}-{course_index + 1:02d}-{index + 1:02d}",
        "title": topic["title"],
        "course_code": course_code,
        "started_at": start.isoformat(),
        "ended_at": messages[-1]["created_at"],
        "tags": topic["tags"],
        "messages": messages,
    }


def build_file(course_code: str, platform: str, account: str) -> list[dict]:
    topics = TOPICS[course_code]
    course_index = list(TOPICS).index(course_code)
    return [build_conversation(course_code, topic, i, course_index) for i, topic in enumerate(topics)]


def write_json(path: Path, platform: str, account: str, conversations: list[dict]) -> None:
    payload = {
        "platform": platform,
        "account_label": account,
        "exported_at": "2026-10-20T21:00:00+08:00",
        "conversations": conversations,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_markdown(path: Path, platform: str, account: str, conversations: list[dict]) -> None:
    lines = [f"平台: {platform}", f"账号: {account}", ""]
    for conv in conversations:
        lines.append(f"# {conv['title']}")
        lines.append(f"- 会话ID: {conv['external_id']}")
        lines.append(f"- 课程: {conv['course_code']}")
        lines.append(f"- 标签: {', '.join(conv['tags'])}")
        lines.append(f"- 开始: {conv['started_at'].replace('T', ' ')[:16]}")
        lines.append(f"- 结束: {conv['ended_at'].replace('T', ' ')[:16]}")
        lines.append("")
        for msg in conv["messages"]:
            label = "用户" if msg["role"] == "user" else "助手"
            stamp = msg["created_at"].replace("T", " ")[:16]
            lines.append(f"## {label} [{stamp}]")
            lines.append(msg["content"])
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_chatgpt_export(path: Path) -> int:
    conversations = build_file("DB", "chatgpt", "alice@chatgpt")[:2]
    exported = []
    for conv in conversations:
        mapping: dict[str, dict] = {
            "root": {"id": "root", "parent": None, "children": [], "message": None}
        }
        parent = "root"
        node_ids = []
        for index, msg in enumerate(conv["messages"], start=1):
            node_id = f"{conv['external_id']}-node-{index}"
            stamp = datetime.fromisoformat(msg["created_at"]).timestamp()
            mapping[node_id] = {
                "id": node_id,
                "parent": parent,
                "children": [],
                "message": {
                    "id": node_id,
                    "author": {"role": msg["role"]},
                    "content": {"content_type": "text", "parts": [msg["content"]]},
                    "create_time": stamp,
                    "status": "finished_successfully",
                },
            }
            mapping[parent]["children"].append(node_id)
            parent = node_id
            node_ids.append(node_id)
        # 一条未进入当前路径的“重新生成”分支，用来验证适配器沿 current_node 回溯。
        alt_id = f"{conv['external_id']}-node-{len(conv['messages']) + 1}-alt"
        mapping[alt_id] = {
            "id": alt_id,
            "parent": node_ids[0],
            "children": [],
            "message": {
                "id": alt_id,
                "author": {"role": "assistant"},
                "content": {
                    "content_type": "text",
                    "parts": ["这是一条没有被采用的重新生成回答。"],
                },
                "create_time": datetime.fromisoformat(conv["started_at"]).timestamp() + 60,
                "status": "finished_successfully",
            },
        }
        mapping[node_ids[0]]["children"].append(alt_id)
        exported.append(
            {
                "id": conv["external_id"],
                "title": conv["title"],
                "create_time": datetime.fromisoformat(conv["started_at"]).timestamp(),
                "update_time": datetime.fromisoformat(conv["ended_at"]).timestamp(),
                "mapping": mapping,
                "current_node": parent,
            }
        )
    path.write_text(
        json.dumps(exported, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return len(exported)


def write_qoder_export(path: Path) -> int:
    conversations = build_file("OS", "qoder", "alice@qoder")[:2]
    sessions = []
    for conv in conversations:
        sessions.append(
            {
                "session_id": conv["external_id"],
                "name": conv["title"],
                "created_at": conv["started_at"],
                "updated_at": conv["ended_at"],
                "course": conv["course_code"],
                "tags": conv["tags"],
                "messages": [
                    {
                        "role": msg["role"],
                        "text": msg["content"],
                        "timestamp": msg["created_at"],
                    }
                    for msg in conv["messages"]
                ],
            }
        )
    payload = {
        "exported_at": "2026-10-21T09:30:00+08:00",
        "account": "alice@qoder",
        "sessions": sessions,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(sessions)


def write_deepseek_markdown(path: Path) -> int:
    conversations = build_file("NET", "deepseek", "alice@deepseek")[:2]
    lines = ["- **Platform:** DeepSeek", "- **Account:** alice@deepseek", ""]
    for conv in conversations:
        lines.extend(
            [
                f"# {conv['title']}",
                f"- **Session ID:** {conv['external_id']}",
                f"- **Course:** {conv['course_code']}",
                f"- **Tags:** {', '.join(conv['tags'])}",
                f"- **Started:** {conv['started_at'].replace('T', ' ')[:16]}",
                f"- **Ended:** {conv['ended_at'].replace('T', ' ')[:16]}",
                "",
            ]
        )
        for msg in conv["messages"]:
            label = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"### {label}")
            lines.append(msg["content"])
            lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return len(conversations)


def write_broken(path: Path) -> None:
    payload = {
        "platform": "unknown-platform",
        "account_label": "broken",
        "conversations": [
            {
                "title": "",
                "messages": [
                    {"role": "robot", "content": "", "created_at": "不是时间"},
                ],
            },
            {
                "external_id": "broken-2",
                "title": "缺少消息的会话",
                "messages": [],
            },
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    SAMPLES.mkdir(parents=True, exist_ok=True)
    total_conversations = 0
    total_messages = 0

    for file_name, course_code, platform, account, kind in FILE_SPECS:
        conversations = build_file(course_code, platform, account)
        path = SAMPLES / file_name
        if kind == "json":
            write_json(path, platform, account, conversations)
        else:
            write_markdown(path, platform, account, conversations)
        total_conversations += len(conversations)
        total_messages += sum(len(conv["messages"]) for conv in conversations)
        print(f"wrote {path.name}: {len(conversations)} conversations")

    shutil.copyfile(SAMPLES / "qoder_db.json", SAMPLES / "duplicate_db.json")
    write_broken(SAMPLES / "broken.json")

    adapter_files = [
        ("chatgpt_export_conversations.json", write_chatgpt_export),
        ("qoder_sessions_export.json", write_qoder_export),
        ("deepseek_export.md", write_deepseek_markdown),
    ]
    for file_name, writer in adapter_files:
        count = writer(SAMPLES / file_name)
        print(f"wrote {file_name}: {count} conversations")

    print(f"total: {total_conversations} conversations, {total_messages} messages")
    print("samples:", ", ".join(sorted(p.name for p in SAMPLES.glob("*"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
