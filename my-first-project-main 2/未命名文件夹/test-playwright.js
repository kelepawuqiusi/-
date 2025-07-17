const { chromium } = require('playwright');

async function testBrowser() {
  console.log('🚀 启动 Playwright 有头浏览器测试...');
  
  // 启动浏览器 - 明确指定 headless: false 确保是有头模式
  const browser = await chromium.launch({ 
    headless: false,  // 确保是有头模式
    channel: 'chrome', // 使用 Chrome
    slowMo: 1000       // 每个操作间隔1秒，便于观察
  });
  
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 }
  });
  
  const page = await context.newPage();
  
  try {
    console.log('📖 导航到百度首页...');
    await page.goto('https://www.baidu.com');
    
    console.log('📷 等待页面加载完成...');
    await page.waitForLoadState('networkidle');
    
    console.log('🔍 查找搜索框并输入测试文字...');
    await page.fill('#kw', 'Playwright 自动化测试');
    
    console.log('📸 截图保存...');
    await page.screenshot({ path: './playwright-output/test-screenshot.png' });
    
    console.log('🎯 点击搜索按钮...');
    await page.click('#su');
    
    console.log('⏱️ 等待搜索结果...');
    await page.waitForTimeout(3000);
    
    console.log('📸 搜索结果截图...');
    await page.screenshot({ path: './playwright-output/search-results.png' });
    
    console.log('✅ 测试完成！浏览器将在5秒后关闭...');
    await page.waitForTimeout(5000);
    
  } catch (error) {
    console.error('❌ 测试出错:', error.message);
  } finally {
    await browser.close();
    console.log('🔚 浏览器已关闭');
  }
}

testBrowser().catch(console.error); 