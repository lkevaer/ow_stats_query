import { defineConfig } from '@tarojs/cli';

export default defineConfig({
  framework: 'react',
  compileMode: 'bundle',
  outputRoot: 'dist',
  sourceRoot: 'src',
  plugins: [
    '@tarojs/plugin-framework-react',
    '@tarojs/plugin-platform-h5',
    '@tarojs/plugin-platform-weapp',
    '@tarojs/plugin-html',
  ],
  alias: {
    '@': './src',
  },
  h5: {
    devServer: {
      port: 10086,
      open: false,
    },
    publicPath: '/',
  },
  weapp: {},
});