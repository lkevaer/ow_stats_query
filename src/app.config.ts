export default defineAppConfig({
  pages: [
    'pages/home/index',
    'pages/matches/index',
    'pages/analyze/index',
    'pages/mine/index',
    'pages/match-detail/index',
  ],
  window: {
    backgroundTextStyle: 'dark',
    navigationBarBackgroundColor: '#0f1420',
    navigationBarTitleText: '守望先锋数据',
    navigationBarTextStyle: 'white',
    backgroundColor: '#0f1420',
    backgroundColorTop: '#0f1420',
    backgroundColorBottom: '#0f1420',
  },
  tabBar: {
    color: '#5a6478',
    selectedColor: '#f9a03f',
    backgroundColor: '#1a2236',
    borderStyle: 'black',
    list: [
      {
        pagePath: 'pages/home/index',
        text: '首页',
        iconPath: 'assets/tabbar/home.svg',
        selectedIconPath: 'assets/tabbar/home-selected.svg',
      },
      {
        pagePath: 'pages/matches/index',
        text: '战绩',
        iconPath: 'assets/tabbar/matches.svg',
        selectedIconPath: 'assets/tabbar/matches-selected.svg',
      },
      {
        pagePath: 'pages/analyze/index',
        text: '分析',
        iconPath: 'assets/tabbar/analyze.svg',
        selectedIconPath: 'assets/tabbar/analyze-selected.svg',
      },
      {
        pagePath: 'pages/mine/index',
        text: '我的',
        iconPath: 'assets/tabbar/mine.svg',
        selectedIconPath: 'assets/tabbar/mine-selected.svg',
      },
    ],
  },
});