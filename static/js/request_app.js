/* ============================================================
   EmbyPulse 玩家社区 - 核心逻辑驱动
   包含：Alpine.js 状态管理、海报生成引擎、API 交互、图表渲染
   ============================================================ */

document.addEventListener('alpine:init', () => {
    // 1. 拖拽滚动指令组件 (用于海报横滑)
    Alpine.data('dragScroll', () => ({
        isDown: false,
        isDragging: false,
        startX: 0,
        scrollLeft: 0,
        start(e) {
            this.isDown = true;
            this.isDragging = false;
            this.startX = e.pageX - this.$el.offsetLeft;
            this.scrollLeft = this.$el.scrollLeft;
        },
        end() {
            this.isDown = false;
            setTimeout(() => { this.isDragging = false; }, 50);
        },
        move(e) {
            if (!this.isDown) return;
            this.isDragging = true;
            e.preventDefault();
            const walk = (e.pageX - this.$el.offsetLeft - this.startX) * 1.5;
            this.$el.scrollLeft = this.scrollLeft - walk;
        }
    }));

    // 2. 主应用 Alpine 状态机
    Alpine.data('requestApp', () => ({
        // --- 基础状态 ---
        scrolled: false,
        lastScrollTop: 0,
        isScrollingDown: false,
        isLoaded: false,
        isLoggedIn: false,
        isDarkMode: false,
        
        // --- 用户与服务器信息 ---
        userId: '',
        userName: '',
        expireDate: '未知',
        serverUrl: '',
        showServerUrl: false,
        loginForm: { username: '', password: '' },
        isLoggingIn: false,
        
        // --- 导航与搜索 ---
        currentTab: 'explore',
        searchQuery: '',
        isSearching: false,
        searchResults: [],
        
        // --- 全站大厅数据 ---
        serverDashboard: null,
        serverLatest: [],
        serverTopRated: [], 
        serverGenres: [],   
        serverTopMovies: [],
        serverTopSeries: [],
        
        // --- 弹窗与 UI 控制 ---
        showcaseModal: { open: false, isLoading: false, data: null },
        queueModal: { open: false, activeTab: 'request' },
        myQueue: [],
        myFeedbacks: [],
        userStats: null,
        userBadges: [],
        userTrend: null,
        isStatsLoading: false,
        statsLoaded: false,
        charts: { hour: null, device: null, client: null, trend: null },
        
        // --- 求片与反馈 ---
        isModalOpen: false,
        activeItem: null,
        tvSeasons: [],
        isLoadingSeasons: false,
        isCheckingLocal: false,
        selectedSeasons: [],
        isSubmitting: false,
        toast: { show: false, message: '', type: 'success' },
        feedbackModal: { open: false, itemName: '', posterPath: '', issueType: '缺少字幕', desc: '' },
        feedbackIssues: ['缺少字幕', '字幕错位', '视频卡顿/花屏', '清晰度太低', '音轨无声/音画不同步', '其他问题'],
        isFeedbackSubmitting: false,

        // --- 专属海报工作室 ---
        posterStudio: {
            open: false,
            isLoading: false,
            isSaving: false,
            period: 'month',
            periodLabel: '本月 观影报告',
            data: null,
            useCoverBg: false,
            top1BgBase64: null
        },

        // ==========================
        // 核心初始化
        // ==========================
        async initTheme() {
            this.isDarkMode = document.documentElement.classList.contains('dark');
            try {
                const res = await fetch('/api/requests/check');
                const data = await res.json();
                if (data.status === 'success') { 
                    this.isLoggedIn = true; 
                    this.userId = data.user.Id;
                    this.userName = data.user.Name;
                    this.expireDate = data.user.expire_date;
                    this.serverUrl = data.server_url;
                    this.loadServerData(); 
                }
            } catch(e) {}
            this.isLoaded = true;
        },

        handleScroll() {
            const st = window.pageYOffset || document.documentElement.scrollTop;
            this.scrolled = st > 50;
            this.isScrollingDown = st > this.lastScrollTop && st > 50;
            this.lastScrollTop = st <= 0 ? 0 : st;
        },

        toggleTheme() {
            this.isDarkMode = !this.isDarkMode;
            localStorage.setItem('ep_theme', this.isDarkMode ? 'dark' : 'light');
            document.documentElement.classList.toggle('dark', this.isDarkMode);
            if (this.currentTab === 'profile' && this.statsLoaded) {
                setTimeout(() => this.renderCharts(), 150);
            }
        },

        showToast(msg, type = 'success') {
            this.toast = { show: true, message: msg, type };
            setTimeout(() => this.toast.show = false, 3000);
        },

        async copyToClipboard(text) {
            try {
                await navigator.clipboard.writeText(text);
            } catch(e) {
                const input = document.createElement('input');
                input.value = text;
                document.body.appendChild(input);
                input.select();
                document.execCommand('copy');
                document.body.removeChild(input);
            }
        },

        // ==========================
        // 用户与登录
        // ==========================
        async login() {
            if(!this.loginForm.username || !this.loginForm.password) return;
            this.isLoggingIn = true;
            try {
                const res = await fetch('/api/requests/auth', { 
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' }, 
                    body: JSON.stringify(this.loginForm) 
                });
                const data = await res.json();
                if (data.status === 'success') {
                    const checkRes = await fetch('/api/requests/check');
                    const checkData = await checkRes.json();
                    if (checkData.status === 'success') { 
                        this.userId = checkData.user.Id; 
                        this.userName = checkData.user.Name; 
                        this.expireDate = checkData.user.expire_date;
                        this.serverUrl = checkData.server_url;
                    }
                    this.isLoggedIn = true; 
                    this.loadServerData();
                    this.showToast('登录成功');
                } else { this.showToast(data.message, 'error'); }
            } catch(e) { this.showToast('网络错误', 'error'); }
            this.isLoggingIn = false;
        },

        async logout() {
            try { 
                await fetch('/api/requests/logout', { method: 'POST' }); 
                this.isLoggedIn = false; 
                this.loginForm.password = ''; 
                this.statsLoaded = false; 
            } catch (e) {}
        },

        // ==========================
        // 首页数据加载
        // ==========================
        async loadServerData() {
            try {
                const [dash, hub, lat, topM, topS] = await Promise.all([
                    fetch('/api/stats/dashboard?user_id=all').then(r => r.json()),
                    fetch('/api/requests/hub_data').then(r => r.json()),
                    fetch('/api/stats/latest?limit=15').then(r => r.json()),
                    fetch('/api/stats/top_movies?category=Movie&sort_by=count').then(r => r.json()),
                    fetch('/api/stats/top_movies?category=Episode&sort_by=count').then(r => r.json())
                ]);
                
                if (dash.status === 'success') this.serverDashboard = dash.data;
                if (hub.status === 'success') {
                    this.serverTopRated = hub.data.top_rated;
                    this.serverGenres = hub.data.genres;
                }
                if (lat.status === 'success') this.serverLatest = lat.data;
                if (topM.status === 'success') this.serverTopMovies = topM.data.slice(0, 10);
                if (topS.status === 'success') this.serverTopSeries = topS.data.slice(0, 10);
            } catch(e) {}
        },

        // ==========================
        // 弹窗与视图切换
        // ==========================
        switchTab(tab) {
            this.currentTab = tab;
            this.$nextTick(() => { window.scrollTo(0, 0); });
            if (tab === 'profile') {
                if (!this.statsLoaded) this.loadProfileStats();
                else setTimeout(() => this.renderCharts(), 150);
            }
        },

        async openShowcaseModal(itemId, fallbackItem = null) {
            this.showcaseModal.data = fallbackItem;
            this.showcaseModal.open = true;
            this.showcaseModal.isLoading = true;
            document.body.style.overflow = 'hidden';
            try {
                const res = await fetch(`/api/requests/item_info?item_id=${itemId}`);
                const data = await res.json();
                if (data.status === 'success') {
                    this.showcaseModal.data = data.data;
                }
            } catch(e) {}
            this.showcaseModal.isLoading = false;
        },

        closeShowcaseModal() {
            this.showcaseModal.open = false;
            document.body.style.overflow = '';
        },

        openQueueModal(tab) {
            this.queueModal.activeTab = tab;
            this.queueModal.open = true;
            document.body.style.overflow = 'hidden';
            if(tab === 'request') this.loadQueue();
            else this.loadMyFeedback();
        },

        closeQueueModal() {
            this.queueModal.open = false;
            document.body.style.overflow = '';
        },

        // ==========================
        // 求片与反馈逻辑
        // ==========================
        async searchMedia() {
            if (!this.searchQuery.trim()) return;
            this.isSearching = true; 
            if (this.currentTab !== 'request') this.currentTab = 'request'; 
            window.scrollTo(0, 0);
            try {
                const res = await fetch(`/api/requests/search?query=${encodeURIComponent(this.searchQuery)}`);
                const data = await res.json();
                if (data.status === 'success') { 
                    this.searchResults = data.data; 
                    if (data.data.length === 0) this.showToast('未找到相关结果', 'error'); 
                } else { this.showToast(data.message, 'error'); }
            } catch (e) { this.showToast('网络错误', 'error'); } finally { this.isSearching = false; }
        },

        async openModal(item) {
            this.activeItem = item;
            this.isModalOpen = true;
            this.tvSeasons = [];
            this.selectedSeasons = [];
            document.body.style.overflow = 'hidden';
            if (item.media_type === 'tv') {
                this.isLoadingSeasons = true;
                try { 
                    const res = await fetch(`/api/requests/tv/${item.tmdb_id}`); 
                    const data = await res.json(); 
                    if (data.status === 'success') { 
                        this.tvSeasons = data.seasons; 
                        if (this.tvSeasons.some(s => s.exists_locally)) {
                            this.activeItem = { ...this.activeItem, local_status: 2 };
                        }
                    } 
                } catch (e) {}
                this.isLoadingSeasons = false;
            } else if (item.media_type === 'movie') {
                this.isCheckingLocal = true;
                try { 
                    const res = await fetch(`/api/requests/check/movie/${item.tmdb_id}`); 
                    const data = await res.json(); 
                    if (data.status === 'success' && data.exists) this.activeItem.local_status = 2; 
                } catch(e) {}
                this.isCheckingLocal = false;
            }
        },

        closeModal() {
            this.isModalOpen = false;
            setTimeout(() => { this.activeItem = null; this.selectedSeasons = []; }, 300);
            document.body.style.overflow = '';
        },

        toggleSelectAllSeasons() {
            const available = this.tvSeasons.filter(s => !s.exists_locally).map(s => s.season_number);
            this.selectedSeasons = (this.selectedSeasons.length === available.length) ? [] : available;
        },

        async submitRequest() {
            if (this.activeItem.media_type === 'movie' && this.activeItem.local_status === 2) return;
            this.isSubmitting = true;
            const payload = { 
                tmdb_id: this.activeItem.tmdb_id, 
                media_type: this.activeItem.media_type, 
                title: this.activeItem.title, 
                year: this.activeItem.year, 
                poster_path: this.activeItem.poster_path, 
                overview: this.activeItem.overview, 
                seasons: this.activeItem.media_type === 'tv' ? this.selectedSeasons.map(Number) : [0] 
            };
            try {
                const res = await fetch('/api/requests/submit', { 
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' }, 
                    body: JSON.stringify(payload) 
                });
                const data = await res.json();
                if (data.status === 'success') { 
                    this.showToast('✅ ' + data.message); 
                    this.closeModal(); 
                    this.openQueueModal('request'); 
                } else { this.showToast('❌ ' + data.message, 'error'); }
            } catch (e) { this.showToast('提交失败，请重试', 'error'); } finally { this.isSubmitting = false; }
        },

        openFeedbackModal(itemName, posterPath = '') {
            this.feedbackModal.itemName = itemName;
            this.feedbackModal.posterPath = posterPath;
            this.feedbackModal.issueType = '缺少字幕';
            this.feedbackModal.desc = '';
            this.feedbackModal.open = true;
            if(this.isModalOpen) this.closeModal();
            if(this.showcaseModal.open) this.closeShowcaseModal();
        },

        async submitFeedback() {
            this.isFeedbackSubmitting = true;
            try {
                const res = await fetch('/api/requests/feedback/submit', {
                    method: 'POST', 
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        item_name: this.feedbackModal.itemName, 
                        issue_type: this.feedbackModal.issueType, 
                        description: this.feedbackModal.desc, 
                        poster_path: this.feedbackModal.posterPath 
                    })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    this.showToast(data.message);
                    this.feedbackModal.open = false;
                    this.openQueueModal('feedback'); 
                } else { this.showToast(data.message, 'error'); }
            } catch(e) { this.showToast('报错失败，请重试', 'error'); }
            finally { this.isFeedbackSubmitting = false; }
        },

        // ==========================
        // 个人数据与图表
        // ==========================
        async loadProfileStats() {
            if (this.statsLoaded || !this.userId) return;
            this.isStatsLoading = true;
            try {
                const [stats, badges, trend] = await Promise.all([
                    fetch(`/api/stats/user_details?user_id=${this.userId}`).then(r => r.json()),
                    fetch(`/api/stats/badges?user_id=${this.userId}`).then(r => r.json()),
                    fetch(`/api/stats/trend?dimension=day&user_id=${this.userId}`).then(r => r.json())
                ]);
                
                if (stats.status === 'success') this.userStats = stats.data;
                if (badges.status === 'success') this.userBadges = badges.data;
                if (trend.status === 'success') this.userTrend = trend.data;

                this.statsLoaded = true; 
                this.renderCharts();
            } catch(e) {}
            this.isStatsLoading = false;
        },

        renderCharts() {
            this.$nextTick(() => {
                if (!window.Chart || !this.userStats) return;
                const isDark = this.isDarkMode;
                const textColor = isDark ? '#a1a1aa' : '#64748b'; 
                const macaronColors = ['#10b981', '#3b82f6', '#8b5cf6', '#6366f1', '#14b8a6', '#64748b'];
                const warmColors = ['#f43f5e', '#f59e0b', '#ec4899', '#f97316', '#d946ef', '#64748b'];
                const borderColor = isDark ? '#000000' : '#ffffff';

                // 小时生物钟
                if (document.getElementById('profileHourChart')) {
                    if (this.charts.hour) this.charts.hour.destroy();
                    const ctx = document.getElementById('profileHourChart').getContext('2d');
                    let labels = [], values = [];
                    for(let i=0; i<24; i++) { 
                        labels.push(String(i).padStart(2, '0')); 
                        values.push(this.userStats.hourly[String(i).padStart(2, '0')] || 0); 
                    }
                    this.charts.hour = new Chart(ctx, { 
                        type: 'bar', 
                        data: { labels, datasets: [{ data: values, backgroundColor: isDark ? '#818cf8' : '#6366f1', borderRadius: 4 }] },
                        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: textColor, maxTicksLimit: 12, font: {size: 9} } }, y: { display: false } } } 
                    });
                }

                // 终端分布
                if (document.getElementById('profileDeviceChart') && this.userStats.devices) {
                    if (this.charts.device) this.charts.device.destroy();
                    const ctx = document.getElementById('profileDeviceChart').getContext('2d');
                    let labels = [], values = [], others = 0;
                    this.userStats.devices.forEach((d, i) => { if(i<4){ labels.push(d.Device); values.push(d.Plays); } else { others += d.Plays; } });
                    if(others > 0){ labels.push('其他'); values.push(others); }
                    this.charts.device = new Chart(ctx, { 
                        type: 'doughnut', 
                        data: { labels, datasets: [{ data: values, backgroundColor: macaronColors, borderWidth: 2, borderColor }] },
                        options: { responsive: true, maintainAspectRatio: false, cutout: '65%', plugins: { legend: { position: 'right', labels: { boxWidth: 6, font: {size: 9}, color: textColor } } } } 
                    });
                }

                // 趋势图
                if (document.getElementById('profileTrendChart') && this.userTrend) {
                    if (this.charts.trend) this.charts.trend.destroy();
                    const ctx = document.getElementById('profileTrendChart').getContext('2d');
                    const labels = Object.keys(this.userTrend).map(k => k.substring(5));
                    const values = Object.values(this.userTrend).map(v => Math.round(v/3600)); 
                    this.charts.trend = new Chart(ctx, { 
                        type: 'line', 
                        data: { labels, datasets: [{ data: values, borderColor: isDark ? '#38bdf8' : '#0ea5e9', backgroundColor: isDark ? 'rgba(56,189,248,0.15)' : 'rgba(14,165,233,0.15)', fill: true, tension: 0.4, borderWidth: 2, pointRadius: 0, pointHoverRadius: 4 }] },
                        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { grid: { display: false }, ticks: { color: textColor, maxTicksLimit: 6, font: {size: 9} } }, y: { display: false, beginAtZero: true } } } 
                    });
                }
            });
        },

        getMoviePct() {
            if (!this.userStats || !this.userStats.preference) return 50;
            const pref = this.userStats.preference;
            const total = pref.movie_plays + pref.episode_plays;
            if (total === 0) return 50;
            return Math.round((pref.movie_plays / total) * 100);
        },

        getPrefText() {
            const pct = this.getMoviePct();
            if (pct === 50 && (!this.userStats || this.userStats.overview.total_plays === 0)) return "尚无观看记录，探索中...";
            if (pct > 70) return "「沉浸长片爱好者，偏爱电影的光影」";
            if (pct < 30) return "「剧情连贯控，追剧是最大乐趣」";
            return "「雨露均沾，电影与剧集我全都要」";
        },

        // ==========================
        // 专属海报工作室引擎
        // ==========================
        async setMyPosterPeriod(period) {
            this.posterStudio.period = period;
            const now = new Date(); const y = now.getFullYear(); const m = now.getMonth() + 1;
            if (period === 'year') this.posterStudio.periodLabel = `${y} 年度观影报告`;
            else if (period === 'month') this.posterStudio.periodLabel = `${y}年${m}月 观影报告`;
            else if (period === 'week') {
                const day = now.getDay() || 7; const start = new Date(now); start.setDate(now.getDate() - day + 1); const end = new Date(now); end.setDate(now.getDate() - day + 7);
                this.posterStudio.periodLabel = `${start.getMonth()+1}月${start.getDate()}日 - ${end.getMonth()+1}月${end.getDate()}日 周报`;
            } else this.posterStudio.periodLabel = '历史全量 观影报告';
            await this.loadMyPosterData();
        },

        async loadMyPosterData() {
            this.posterStudio.isLoading = true;
            try {
                // 1. 头像处理
                const avatarEl = document.getElementById('my-p-avatar');
                const b64Avatar = await toBase64(`/api/proxy/user_image/${this.userId}`);
                if (b64Avatar) { avatarEl.style.backgroundImage = `url('${b64Avatar}')`; avatarEl.innerHTML = ''; } 
                else {
                    const hash = this.userName.charCodeAt(0) % 10;
                    const colors = ['#FFC0CB', '#FFB6C1', '#FF69B4', '#E6E6FA', '#D8BFD8', '#B0C4DE', '#ADD8E6', '#87CEFA', '#98FB98', '#90EE90'];
                    avatarEl.style.backgroundColor = colors[hash];
                    avatarEl.innerHTML = `<i class="fa-solid fa-user-astronaut"></i>`;
                }

                // 2. 数据拉取
                const res = await fetch(`/api/stats/poster_data?user_id=${this.userId}&period=${this.posterStudio.period}`);
                const json = await res.json();
                const data = json.data;
                this.posterStudio.data = data;
                this.posterStudio.top1BgBase64 = null;

                if (data.plays > 0) {
                    const list = data.top_list;
                    // 渲染前三名
                    const renderRank = async (rank, idx) => {
                        if(list[idx]) {
                            const realImg = document.getElementById(`my-rank${rank}-img`);
                            realImg.removeAttribute('data-fallback-done');
                            const b64 = await toBase64(`/api/proxy/smart_image?item_id=${list[idx].ItemId}&type=Primary`);
                            if(b64) {
                                realImg.src = b64; realImg.style.objectFit = "cover"; realImg.style.padding = "0";
                                if(rank === 1) {
                                    this.posterStudio.top1BgBase64 = await applyPhysicalBlur(b64);
                                    if(this.posterStudio.useCoverBg) document.getElementById('my-poster-bg-img').style.backgroundImage = `url('${this.posterStudio.top1BgBase64}')`;
                                }
                            } else {
                                window.fallbackReportPoster(realImg, list[idx].ItemName);
                            }
                        }
                    };
                    await Promise.all([renderRank(1, 0), renderRank(2, 1), renderRank(3, 2)]);

                    // 渲染列表小图
                    const smPromises = [];
                    const max = Math.min(list.length, 10);
                    for(let i=3; i<max; i++) {
                        smPromises.push((async () => {
                            const b64 = await toBase64(`/api/proxy/smart_image?item_id=${list[i].ItemId}&type=Primary`);
                            const imgEl = document.getElementById(`my-sm-img-${i-3}`);
                            if(imgEl) {
                                if(b64) { imgEl.src = b64; imgEl.style.objectFit = "cover"; } 
                                else { window.fallbackReportPoster(imgEl, list[i].ItemName); }
                            }
                        })());
                    }
                    await Promise.all(smPromises);

                    // 3. 结构化情绪看板渲染
                    const area = document.getElementById('my-mood-area');
                    area.innerHTML = '';
                    let html = '';
                    const mood = data.mood_data;
                    if(mood) {
                        if(mood.genres && mood.genres.length > 0) {
                            const iconMap = {'剧情': '🎬', '喜剧': '😂', '动作': '⚔️', '科幻': '🛸', '悬疑': '🕵️‍♂️', '爱情': '❤️', '动画': '🦄', '恐怖': '👻', '纪录': '🌍'};
                            let tagsHtml = '';
                            mood.genres.forEach(g => {
                                let icon = iconMap[g] || '🏷️';
                                tagsHtml += `<div class="my-mood-tag-pill"><span>${icon}</span> <span>${g}</span></div>`;
                            });
                            html += `<div class="my-mood-card"><div class="my-mood-title"><i class="fa-solid fa-flask"></i> 观影基因重组</div><div class="my-mood-tags-container">${tagsHtml}</div><div class="my-mood-desc">这是一个独一无二的影视灵魂宇宙。</div></div>`;
                        }
                        if(mood.binge_day) {
                            html += `<div class="my-mood-card"><div class="my-mood-title"><i class="fa-solid fa-fire"></i> 极度沉迷时刻</div><div class="my-mood-data-container"><div class="my-mood-data-box"><div class="my-mood-data-val">${mood.binge_day.date}</div><div class="my-mood-data-sub">这一天最疯狂</div></div><div class="my-mood-data-box"><div class="my-mood-data-val">${mood.binge_day.hours} H</div><div class="my-mood-data-sub">一口气看了</div></div></div><div class="my-mood-desc">是谁陪你一起度过了这漫长的时光？</div></div>`;
                        }
                        if(mood.late_night) {
                            html += `<div class="my-mood-card"><div class="my-mood-title"><i class="fa-solid fa-owl"></i> 深夜刺客出没</div><div class="my-mood-data-container"><div class="my-mood-data-box" style="flex:1;"><div class="my-mood-data-val">凌晨 ${mood.late_night.time}</div><div class="my-mood-data-sub">在看: ${mood.late_night.name}</div></div></div><div class="my-mood-desc">整座城市都在沉睡，你却独自在此修仙。</div></div>`;
                        }
                    }
                    area.innerHTML = html;
                }
                
                // 自动适配预览缩放
                this.$nextTick(() => {
                    const wrapper = document.getElementById('my-poster-preview-area');
                    const scaleWrapper = document.getElementById('my-scale-wrapper');
                    const scale = Math.min((wrapper.clientWidth - 40) / 400, 1);
                    scaleWrapper.style.transform = `scale(${scale})`;
                });
            } catch(e) {}
            this.posterStudio.isLoading = false;
        },

        async saveMyPoster() {
            this.posterStudio.isSaving = true;
            const scaleWrapper = document.getElementById('my-scale-wrapper');
            const oldT = scaleWrapper.style.transform;
            document.getElementById('my-poster-preview-area').scrollTo(0, 0);
            scaleWrapper.style.transform = 'none';
            await new Promise(r => setTimeout(r, 400)); 
            try {
                const canvas = await html2canvas(document.getElementById('my-capture-target'), { 
                    scale: 2, useCORS: true, backgroundColor: null, scrollY: 0, scrollX: 0 
                });
                const link = document.createElement('a');
                link.download = `EmbyPulse_${this.userName}_Poster.png`;
                link.href = canvas.toDataURL();
                link.click();
                this.showToast('海报已保存到相册！');
            } catch(e) { this.showToast('生成失败', 'error'); } 
            finally { 
                scaleWrapper.style.transform = oldT; 
                this.posterStudio.isSaving = false;
            }
        },

        // --- 工单辅助 ---
        async loadQueue() {
            try { const res = await fetch('/api/requests/my'); const data = await res.json(); if (data.status === 'success') this.myQueue = data.data; } catch (e) {}
        },
        async loadMyFeedback() {
            try { const res = await fetch('/api/requests/feedback/my'); const data = await res.json(); if (data.status === 'success') this.myFeedbacks = data.data; } catch (e) {}
        }
    }));
});

/**
 * 物理级高斯模糊 Canvas 引擎
 * 解决 html2canvas 不支持 CSS Blur 滤镜的顽疾
 */
async function applyPhysicalBlur(base64Url) {
    return new Promise((resolve) => {
        const img = new Image();
        img.onload = () => {
            const canvas = document.createElement('canvas');
            const ctx = canvas.getContext('2d');
            canvas.width = 400; 
            canvas.height = 800; 
            ctx.filter = 'blur(40px) brightness(0.4)';
            const scale = Math.max(canvas.width / img.width, canvas.height / img.height);
            const x = (canvas.width / 2) - (img.width / 2) * scale;
            const y = (canvas.height / 2) - (img.height / 2) * scale;
            ctx.drawImage(img, x, y, img.width * scale, img.height * scale);
            resolve(canvas.toDataURL('image/jpeg', 0.8));
        };
        img.onerror = () => resolve(base64Url);
        img.src = base64Url;
    });
}