from typing import Any, List, Dict, Optional
import asyncio
import json
import os
import pandas as pd
from datetime import datetime
from playwright.async_api import async_playwright
from fastmcp import FastMCP
import re
import glob
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# 初始化 FastMCP 服务器
mcp = FastMCP("xiaohongshu_scraper")

# 全局变量
BROWSER_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "browser_data")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")

# 确保目录存在
os.makedirs(BROWSER_DATA_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

# 用于存储浏览器上下文，以便在不同方法之间共享
browser_context = None
main_page = None
is_logged_in = False

async def ensure_browser():
    """确保浏览器已启动并登录"""
    global browser_context, main_page, is_logged_in
    
    if browser_context is None:
        # 启动浏览器
        playwright_instance = await async_playwright().start()
        
        # 使用持久化上下文来保存用户状态
        browser_context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=BROWSER_DATA_DIR,
            headless=False,  # 非隐藏模式，方便用户登录
            viewport={"width": 1280, "height": 800},
            timeout=60000
        )
        
        # 创建一个新页面
        if browser_context.pages:
            main_page = browser_context.pages[0]
        else:
            main_page = await browser_context.new_page()
        
        # 设置页面级别的超时时间
        main_page.set_default_timeout(60000)
    
    # 检查登录状态
    if not is_logged_in:
        # 访问小红书首页
        await main_page.goto("https://www.xiaohongshu.com", timeout=60000)
        await asyncio.sleep(3)
        
        # 检查是否已登录
        login_elements = await main_page.query_selector_all('text="登录"')
        if login_elements:
            return False  # 需要登录
        else:
            is_logged_in = True
            return True  # 已登录
    
    return True

@mcp.tool()
async def login() -> str:
    """登录小红书账号"""
    global is_logged_in
    
    await ensure_browser()
    
    if is_logged_in:
        return "已登录小红书账号"
    
    # 访问小红书登录页面
    await main_page.goto("https://www.xiaohongshu.com", timeout=60000)
    await asyncio.sleep(3)
    
    # 查找登录按钮并点击
    login_elements = await main_page.query_selector_all('text="登录"')
    if login_elements:
        await login_elements[0].click()
        
        # 提示用户手动登录
        message = "请在打开的浏览器窗口中完成登录操作。登录成功后，系统将自动继续。"
        
        # 等待用户登录成功
        max_wait_time = 180  # 等待3分钟
        wait_interval = 5
        waited_time = 0
        
        while waited_time < max_wait_time:
            # 检查是否已登录成功
            still_login = await main_page.query_selector_all('text="登录"')
            if not still_login:
                is_logged_in = True
                await asyncio.sleep(2)  # 等待页面加载
                return "登录成功！"
            
            # 继续等待
            await asyncio.sleep(wait_interval)
            waited_time += wait_interval
        
        return "登录等待超时。请重试或手动登录后再使用其他功能。"
    else:
        is_logged_in = True
        return "已登录小红书账号"

@mcp.tool()
async def search_notes(keywords: str, limit: int = 5) -> str:
    """
    根据关键词搜索小红书笔记，返回前limit条结果。
    """
    global main_page
    return await do_search_notes(main_page, keywords, limit)

async def do_search_notes(main_page, keywords: str, limit: int = 5) -> str:
    """
    实际执行小红书搜索并返回前limit条结果。
    """
    # 跳转到搜索页面
    await main_page.goto(f"https://www.xiaohongshu.com/search_result?keyword={keywords}", timeout=60000)
    await asyncio.sleep(5)  # 等待页面加载
    # 以下为原search_notes核心爬取逻辑的简化版
    try:
        # 尝试获取帖子卡片
        post_cards = await main_page.query_selector_all('section.note-item')
        if not post_cards:
            post_cards = await main_page.query_selector_all('div[data-v-a264b01a]')
        post_links = []
        post_titles = []
        for card in post_cards:
            try:
                link_element = await card.query_selector('a[href*="/explore/"]')
                if not link_element:
                    continue
                href = await link_element.get_attribute('href')
                if href and '/explore/' in href:
                    full_url = f"https://www.xiaohongshu.com{href}"
                    post_links.append(full_url)
                    # 获取标题
                    title_element = await card.query_selector('a.title span')
                    if title_element:
                        title = await title_element.text_content()
                    else:
                        title = "未知标题"
                    post_titles.append(title)
            except Exception:
                continue
        # 去重
        unique_posts = []
        seen_urls = set()
        for url, title in zip(post_links, post_titles):
            if url not in seen_urls:
                seen_urls.add(url)
                unique_posts.append({"url": url, "title": title})
        unique_posts = unique_posts[:limit]
        if unique_posts:
            result = "搜索结果：\n\n"
            for i, post in enumerate(unique_posts, 1):
                result += f"{i}. {post['title']}\n   链接: {post['url']}\n\n"
            return result
        else:
            return f"未找到与\"{keywords}\"相关的笔记"
    except Exception as e:
        return f"搜索笔记时出错: {str(e)}"

@mcp.tool()
async def get_note_content(url: str) -> str:
    """获取笔记内容
    
    Args:
        url: 笔记 URL
    """
    login_status = await ensure_browser()
    if not login_status:
        return "请先登录小红书账号"
    
    try:
        # 访问帖子链接
        await main_page.goto(url, timeout=60000)
        await asyncio.sleep(10)  # 增加等待时间到10秒
        
        # 增强滚动操作以确保所有内容加载
        await main_page.evaluate('''
            () => {
                // 先滚动到页面底部
                window.scrollTo(0, document.body.scrollHeight);
                setTimeout(() => { 
                    // 然后滚动到中间
                    window.scrollTo(0, document.body.scrollHeight / 2); 
                }, 1000);
                setTimeout(() => { 
                    // 最后回到顶部
                    window.scrollTo(0, 0); 
                }, 2000);
            }
        ''')
        await asyncio.sleep(3)  # 等待滚动完成和内容加载
        
        # 打印页面结构片段用于分析
        try:
            print("打印页面结构片段用于分析")
            page_structure = await main_page.evaluate('''
                () => {
                    // 获取笔记内容区域
                    const noteContent = document.querySelector('.note-content');
                    const detailDesc = document.querySelector('#detail-desc');
                    const commentArea = document.querySelector('.comments-container, .comment-list');
                    
                    return {
                        hasNoteContent: !!noteContent,
                        hasDetailDesc: !!detailDesc,
                        hasCommentArea: !!commentArea,
                        noteContentHtml: noteContent ? noteContent.outerHTML.slice(0, 500) : null,
                        detailDescHtml: detailDesc ? detailDesc.outerHTML.slice(0, 500) : null,
                        commentAreaFirstChild: commentArea ? 
                            (commentArea.firstElementChild ? commentArea.firstElementChild.outerHTML.slice(0, 500) : null) : null
                    };
                }
            ''')
            print(f"页面结构分析: {json.dumps(page_structure, ensure_ascii=False, indent=2)}")
        except Exception as e:
            print(f"打印页面结构时出错: {str(e)}")
        
        # 获取帖子内容
        post_content = {}
        
        # 获取帖子标题 - 方法1：使用id选择器
        try:
            print("尝试获取标题 - 方法1：使用id选择器")
            title_element = await main_page.query_selector('#detail-title')
            if title_element:
                title = await title_element.text_content()
                post_content["标题"] = title.strip() if title else "未知标题"
                print(f"方法1获取到标题: {post_content['标题']}")
            else:
                print("方法1未找到标题元素")
                post_content["标题"] = "未知标题"
        except Exception as e:
            print(f"方法1获取标题出错: {str(e)}")
            post_content["标题"] = "未知标题"
        
        # 获取帖子标题 - 方法2：使用class选择器
        if post_content["标题"] == "未知标题":
            try:
                print("尝试获取标题 - 方法2：使用class选择器")
                title_element = await main_page.query_selector('div.title')
                if title_element:
                    title = await title_element.text_content()
                    post_content["标题"] = title.strip() if title else "未知标题"
                    print(f"方法2获取到标题: {post_content['标题']}")
                else:
                    print("方法2未找到标题元素")
            except Exception as e:
                print(f"方法2获取标题出错: {str(e)}")
        
        # 获取帖子标题 - 方法3：使用JavaScript
        if post_content["标题"] == "未知标题":
            try:
                print("尝试获取标题 - 方法3：使用JavaScript")
                title = await main_page.evaluate('''
                    () => {
                        // 尝试多种可能的标题选择器
                        const selectors = [
                            '#detail-title',
                            'div.title',
                            'h1',
                            'div.note-content div.title'
                        ];
                        
                        for (const selector of selectors) {
                            const el = document.querySelector(selector);
                            if (el && el.textContent.trim()) {
                                return el.textContent.trim();
                            }
                        }
                        return null;
                    }
                ''')
                if title:
                    post_content["标题"] = title
                    print(f"方法3获取到标题: {post_content['标题']}")
                else:
                    print("方法3未找到标题元素")
            except Exception as e:
                print(f"方法3获取标题出错: {str(e)}")
        
        # 获取作者 - 方法1：使用username类选择器
        try:
            print("尝试获取作者 - 方法1：使用username类选择器")
            author_element = await main_page.query_selector('span.username')
            if author_element:
                author = await author_element.text_content()
                post_content["作者"] = author.strip() if author else "未知作者"
                print(f"方法1获取到作者: {post_content['作者']}")
            else:
                print("方法1未找到作者元素")
                post_content["作者"] = "未知作者"
        except Exception as e:
            print(f"方法1获取作者出错: {str(e)}")
            post_content["作者"] = "未知作者"
        
        # 获取作者 - 方法2：使用链接选择器
        if post_content["作者"] == "未知作者":
            try:
                print("尝试获取作者 - 方法2：使用链接选择器")
                author_element = await main_page.query_selector('a.name')
                if author_element:
                    author = await author_element.text_content()
                    post_content["作者"] = author.strip() if author else "未知作者"
                    print(f"方法2获取到作者: {post_content['作者']}")
                else:
                    print("方法2未找到作者元素")
            except Exception as e:
                print(f"方法2获取作者出错: {str(e)}")
        
        # 获取作者 - 方法3：使用JavaScript
        if post_content["作者"] == "未知作者":
            try:
                print("尝试获取作者 - 方法3：使用JavaScript")
                author = await main_page.evaluate('''
                    () => {
                        // 尝试多种可能的作者选择器
                        const selectors = [
                            'span.username',
                            'a.name',
                            '.author-wrapper .username',
                            '.info .name'
                        ];
                        
                        for (const selector of selectors) {
                            const el = document.querySelector(selector);
                            if (el && el.textContent.trim()) {
                                return el.textContent.trim();
                            }
                        }
                        return null;
                    }
                ''')
                if author:
                    post_content["作者"] = author
                    print(f"方法3获取到作者: {post_content['作者']}")
                else:
                    print("方法3未找到作者元素")
            except Exception as e:
                print(f"方法3获取作者出错: {str(e)}")
        
        # 获取发布时间 - 方法1：使用date类选择器
        try:
            print("尝试获取发布时间 - 方法1：使用date类选择器")
            time_element = await main_page.query_selector('span.date')
            if time_element:
                time_text = await time_element.text_content()
                post_content["发布时间"] = time_text.strip() if time_text else "未知"
                print(f"方法1获取到发布时间: {post_content['发布时间']}")
            else:
                print("方法1未找到发布时间元素")
                post_content["发布时间"] = "未知"
        except Exception as e:
            print(f"方法1获取发布时间出错: {str(e)}")
            post_content["发布时间"] = "未知"
        
        # 获取发布时间 - 方法2：使用正则表达式匹配
        if post_content["发布时间"] == "未知":
            try:
                print("尝试获取发布时间 - 方法2：使用正则表达式匹配")
                time_selectors = [
                    'text=/编辑于/',
                    'text=/\\d{2}-\\d{2}/',
                    'text=/\\d{4}-\\d{2}-\\d{2}/',
                    'text=/\\d+月\\d+日/',
                    'text=/\\d+天前/',
                    'text=/\\d+小时前/',
                    'text=/今天/',
                    'text=/昨天/'
                ]
                
                for selector in time_selectors:
                    time_element = await main_page.query_selector(selector)
                    if time_element:
                        time_text = await time_element.text_content()
                        post_content["发布时间"] = time_text.strip() if time_text else "未知"
                        print(f"方法2获取到发布时间: {post_content['发布时间']}")
                        break
                    else:
                        print(f"方法2未找到发布时间元素: {selector}")
            except Exception as e:
                print(f"方法2获取发布时间出错: {str(e)}")
        
        # 获取发布时间 - 方法3：使用JavaScript
        if post_content["发布时间"] == "未知":
            try:
                print("尝试获取发布时间 - 方法3：使用JavaScript")
                time_text = await main_page.evaluate('''
                    () => {
                        // 尝试多种可能的时间选择器
                        const selectors = [
                            'span.date',
                            '.bottom-container .date',
                            '.date'
                        ];
                        
                        for (const selector of selectors) {
                            const el = document.querySelector(selector);
                            if (el && el.textContent.trim()) {
                                return el.textContent.trim();
                            }
                        }
                        
                        // 尝试查找包含日期格式的文本
                        const dateRegexes = [
                            /编辑于\s*([\d-]+)/,
                            /(\d{2}-\d{2})/,
                            /(\d{4}-\d{2}-\d{2})/,
                            /(\d+月\d+日)/,
                            /(\d+天前)/,
                            /(\d+小时前)/,
                            /(今天)/,
                            /(昨天)/
                        ];
                        
                        const allText = document.body.textContent;
                        for (const regex of dateRegexes) {
                            const match = allText.match(regex);
                            if (match) {
                                return match[0];
                            }
                        }
                        
                        return null;
                    }
                ''')
                if time_text:
                    post_content["发布时间"] = time_text
                    print(f"方法3获取到发布时间: {post_content['发布时间']}")
                else:
                    print("方法3未找到发布时间元素")
            except Exception as e:
                print(f"方法3获取发布时间出错: {str(e)}")
        
        # 获取帖子正文内容 - 方法1：使用精确的ID和class选择器
        try:
            print("尝试获取正文内容 - 方法1：使用精确的ID和class选择器")
            
            # 先明确标记评论区域
            await main_page.evaluate('''
                () => {
                    const commentSelectors = [
                        '.comments-container', 
                        '.comment-list',
                        '.feed-comment',
                        'div[data-v-aed4aacc]',  // 根据您提供的评论HTML结构
                        '.content span.note-text'  // 评论中的note-text结构
                    ];
                    
                    for (const selector of commentSelectors) {
                        const elements = document.querySelectorAll(selector);
                        elements.forEach(el => {
                            if (el) {
                                el.setAttribute('data-is-comment', 'true');
                                console.log('标记评论区域:', el.tagName, el.className);
                            }
                        });
                    }
                }
            ''')
            
            # 先尝试获取detail-desc和note-text组合
            content_element = await main_page.query_selector('#detail-desc .note-text')
            if content_element:
                # 检查是否在评论区域内
                is_in_comment = await content_element.evaluate('(el) => !!el.closest("[data-is-comment=\'true\']") || false')
                if not is_in_comment:
                    content_text = await content_element.text_content()
                    if content_text and len(content_text.strip()) > 50:  # 增加长度阈值
                        post_content["内容"] = content_text.strip()
                        print(f"方法1获取到正文内容，长度: {len(post_content['内容'])}")
                    else:
                        print(f"方法1获取到的内容太短: {len(content_text.strip() if content_text else 0)}")
                        post_content["内容"] = "未能获取内容"
                else:
                    print("方法1找到的元素在评论区域内，跳过")
                    post_content["内容"] = "未能获取内容"
            else:
                print("方法1未找到正文内容元素")
                post_content["内容"] = "未能获取内容"
        except Exception as e:
            print(f"方法1获取正文内容出错: {str(e)}")
            post_content["内容"] = "未能获取内容"
        
        # 获取帖子正文内容 - 方法2：使用XPath选择器
        if post_content["内容"] == "未能获取内容":
            try:
                print("尝试获取正文内容 - 方法2：使用XPath选择器")
                # 使用XPath获取笔记内容区域
                content_text = await main_page.evaluate('''
                    () => {
                        const xpath = '//div[@id="detail-desc"]/span[@class="note-text"]';
                        const result = document.evaluate(xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null);
                        const element = result.singleNodeValue;
                        return element ? element.textContent.trim() : null;
                    }
                ''')
                
                if content_text and len(content_text) > 20:
                    post_content["内容"] = content_text
                    print(f"方法2获取到正文内容，长度: {len(post_content['内容'])}")
                else:
                    print(f"方法2获取到的内容太短或为空: {len(content_text) if content_text else 0}")
            except Exception as e:
                print(f"方法2获取正文内容出错: {str(e)}")
        
        # 获取帖子正文内容 - 方法3：使用JavaScript获取最长文本
        if post_content["内容"] == "未能获取内容":
            try:
                print("尝试获取正文内容 - 方法3：使用JavaScript获取最长文本")
                content_text = await main_page.evaluate('''
                    () => {
                        // 定义评论区域选择器
                        const commentSelectors = [
                            '.comments-container', 
                            '.comment-list',
                            '.feed-comment',
                            'div[data-v-aed4aacc]',
                            '.comment-item',
                            '[data-is-comment="true"]'
                        ];
                        
                        // 找到所有评论区域
                        let commentAreas = [];
                        for (const selector of commentSelectors) {
                            const elements = document.querySelectorAll(selector);
                            elements.forEach(el => commentAreas.push(el));
                        }
                        
                        // 查找可能的内容元素，排除评论区
                        const contentElements = Array.from(document.querySelectorAll('div#detail-desc, div.note-content, div.desc, span.note-text'))
                            .filter(el => {
                                // 检查是否在评论区域内
                                const isInComment = commentAreas.some(commentArea => 
                                    commentArea && commentArea.contains(el));
                                
                                if (isInComment) {
                                    console.log('排除评论区域内容:', el.tagName, el.className);
                                    return false;
                                }
                                
                                const text = el.textContent.trim();
                                return text.length > 100 && text.length < 10000;
                            })
                            .sort((a, b) => b.textContent.length - a.textContent.length);
                        
                        if (contentElements.length > 0) {
                            console.log('找到内容元素:', contentElements[0].tagName, contentElements[0].className);
                            return contentElements[0].textContent.trim();
                        }
                        
                        return null;
                    }
                ''')
                
                if content_text and len(content_text) > 100:  # 增加长度阈值
                    post_content["内容"] = content_text
                    print(f"方法3获取到正文内容，长度: {len(post_content['内容'])}")
                else:
                    print(f"方法3获取到的内容太短或为空: {len(content_text) if content_text else 0}")
            except Exception as e:
                print(f"方法3获取正文内容出错: {str(e)}")
        
        # 获取帖子正文内容 - 方法4：区分正文和评论内容
        if post_content["内容"] == "未能获取内容":
            try:
                print("尝试获取正文内容 - 方法4：区分正文和评论内容")
                content_text = await main_page.evaluate('''
                    () => {
                        // 首先尝试获取note-content区域
                        const noteContent = document.querySelector('.note-content');
                        if (noteContent) {
                            // 查找note-text，这通常包含主要内容
                            const noteText = noteContent.querySelector('.note-text');
                            if (noteText && noteText.textContent.trim().length > 50) {
                                return noteText.textContent.trim();
                            }
                            
                            // 如果没有找到note-text或内容太短，返回整个note-content
                            if (noteContent.textContent.trim().length > 50) {
                                return noteContent.textContent.trim();
                            }
                        }
                        
                        // 如果上面的方法都失败了，尝试获取所有段落并拼接
                        const paragraphs = Array.from(document.querySelectorAll('p'))
                            .filter(p => {
                                // 排除评论区段落
                                const isInComments = p.closest('.comments-container, .comment-list');
                                return !isInComments && p.textContent.trim().length > 10;
                            });
                            
                        if (paragraphs.length > 0) {
                            return paragraphs.map(p => p.textContent.trim()).join('\n\n');
                        }
                        
                        return null;
                    }
                ''')
                
                if content_text and len(content_text) > 50:
                    post_content["内容"] = content_text
                    print(f"方法4获取到正文内容，长度: {len(post_content['内容'])}")
                else:
                    print(f"方法4获取到的内容太短或为空: {len(content_text) if content_text else 0}")
            except Exception as e:
                print(f"方法4获取正文内容出错: {str(e)}")
        
        # 获取帖子正文内容 - 方法5：直接通过DOM结构定位
        if post_content["内容"] == "未能获取内容":
            try:
                print("尝试获取正文内容 - 方法5：直接通过DOM结构定位")
                content_text = await main_page.evaluate('''
                    () => {
                        // 根据您提供的HTML结构直接定位
                        const noteContent = document.querySelector('div.note-content');
                        if (noteContent) {
                            const detailTitle = noteContent.querySelector('#detail-title');
                            const detailDesc = noteContent.querySelector('#detail-desc');
                            
                            if (detailDesc) {
                                const noteText = detailDesc.querySelector('span.note-text');
                                if (noteText) {
                                    return noteText.textContent.trim();
                                }
                                return detailDesc.textContent.trim();
                            }
                        }
                        
                        // 尝试其他可能的结构
                        const descElements = document.querySelectorAll('div.desc');
                        for (const desc of descElements) {
                            // 检查是否在评论区
                            const isInComment = desc.closest('.comments-container, .comment-list, .feed-comment');
                            if (!isInComment && desc.textContent.trim().length > 100) {
                                return desc.textContent.trim();
                            }
                        }
                        
                        return null;
                    }
                ''')
                
                if content_text and len(content_text) > 100:
                    post_content["内容"] = content_text
                    print(f"方法5获取到正文内容，长度: {len(post_content['内容'])}")
                else:
                    print(f"方法5获取到的内容太短或为空: {len(content_text) if content_text else 0}")
            except Exception as e:
                print(f"方法5获取正文内容出错: {str(e)}")
        
        # 格式化返回结果
        result = f"标题: {post_content['标题']}\n"
        result += f"作者: {post_content['作者']}\n"
        result += f"发布时间: {post_content['发布时间']}\n"
        result += f"链接: {url}\n\n"
        result += f"内容:\n{post_content['内容']}"
        
        return result
    
    except Exception as e:
        return f"获取笔记内容时出错: {str(e)}"

@mcp.tool()
async def get_note_comments(url: str) -> str:
    """获取笔记评论
    
    Args:
        url: 笔记 URL
    """
    login_status = await ensure_browser()
    if not login_status:
        return "请先登录小红书账号"
    
    try:
        # 访问帖子链接
        await main_page.goto(url, timeout=60000)
        await asyncio.sleep(5)  # 等待页面加载
        
        # 先滚动到评论区
        comment_section_locators = [
            main_page.get_by_text("条评论", exact=False),
            main_page.get_by_text("评论", exact=False),
            main_page.locator("text=评论").first
        ]
        
        for locator in comment_section_locators:
            try:
                if await locator.count() > 0:
                    await locator.scroll_into_view_if_needed(timeout=5000)
                    await asyncio.sleep(2)
                    break
            except Exception:
                continue
        
        # 滚动页面以加载更多评论
        for i in range(8):
            try:
                await main_page.evaluate("window.scrollBy(0, 500)")
                await asyncio.sleep(1)
                
                # 尝试点击"查看更多评论"按钮
                more_comment_selectors = [
                    "text=查看更多评论",
                    "text=展开更多评论",
                    "text=加载更多",
                    "text=查看全部"
                ]
                
                for selector in more_comment_selectors:
                    try:
                        more_btn = main_page.locator(selector).first
                        if await more_btn.count() > 0 and await more_btn.is_visible():
                            await more_btn.click()
                            await asyncio.sleep(2)
                    except Exception:
                        continue
            except Exception:
                pass
        
        # 获取评论
        comments = []
        
        # 使用特定评论选择器
        comment_selectors = [
            "div.comment-item", 
            "div.commentItem",
            "div.comment-content",
            "div.comment-wrapper",
            "section.comment",
            "div.feed-comment"
        ]
        
        for selector in comment_selectors:
            comment_elements = main_page.locator(selector)
            count = await comment_elements.count()
            if count > 0:
                for i in range(count):
                    try:
                        comment_element = comment_elements.nth(i)
                        
                        # 提取评论者名称
                        username = "未知用户"
                        username_selectors = ["span.user-name", "a.name", "div.username", "span.nickname", "a.user-nickname"]
                        for username_selector in username_selectors:
                            username_el = comment_element.locator(username_selector).first
                            if await username_el.count() > 0:
                                username = await username_el.text_content()
                                username = username.strip()
                                break
                        
                        # 如果没有找到，尝试通过用户链接查找
                        if username == "未知用户":
                            user_link = comment_element.locator('a[href*="/user/profile/"]').first
                            if await user_link.count() > 0:
                                username = await user_link.text_content()
                                username = username.strip()
                        
                        # 提取评论内容
                        content = "未知内容"
                        content_selectors = ["div.content", "p.content", "div.text", "span.content", "div.comment-text"]
                        for content_selector in content_selectors:
                            content_el = comment_element.locator(content_selector).first
                            if await content_el.count() > 0:
                                content = await content_el.text_content()
                                content = content.strip()
                                break
                        
                        # 如果没有找到内容，可能内容就在评论元素本身
                        if content == "未知内容":
                            full_text = await comment_element.text_content()
                            if username != "未知用户" and username in full_text:
                                content = full_text.replace(username, "").strip()
                            else:
                                content = full_text.strip()
                        
                        # 提取评论时间
                        time_location = "未知时间"
                        time_selectors = ["span.time", "div.time", "span.date", "div.date", "time"]
                        for time_selector in time_selectors:
                            time_el = comment_element.locator(time_selector).first
                            if await time_el.count() > 0:
                                time_location = await time_el.text_content()
                                time_location = time_location.strip()
                                break
                        
                        # 如果内容有足够长度且找到用户名，添加评论
                        if username != "未知用户" and content != "未知内容" and len(content) > 2:
                            comments.append({
                                "用户名": username,
                                "内容": content,
                                "时间": time_location
                            })
                    except Exception:
                        continue
                
                # 如果找到了评论，就不继续尝试其他选择器了
                if comments:
                    break
        
        # 如果没有找到评论，尝试使用其他方法
        if not comments:
            # 获取所有用户名元素
            username_elements = main_page.locator('a[href*="/user/profile/"]')
            username_count = await username_elements.count()
            
            if username_count > 0:
                for i in range(username_count):
                    try:
                        username_element = username_elements.nth(i)
                        username = await username_element.text_content()
                        
                        # 尝试获取评论内容
                        content = await main_page.evaluate('''
                            (usernameElement) => {
                                const parent = usernameElement.parentElement;
                                if (!parent) return null;
                                
                                // 尝试获取同级的下一个元素
                                let sibling = usernameElement.nextElementSibling;
                                while (sibling) {
                                    const text = sibling.textContent.trim();
                                    if (text) return text;
                                    sibling = sibling.nextElementSibling;
                                }
                                
                                // 尝试获取父元素的文本，并过滤掉用户名
                                const allText = parent.textContent.trim();
                                if (allText && allText.includes(usernameElement.textContent.trim())) {
                                    return allText.replace(usernameElement.textContent.trim(), '').trim();
                                }
                                
                                return null;
                            }
                        ''', username_element)
                        
                        if username and content:
                            comments.append({
                                "用户名": username.strip(),
                                "内容": content.strip(),
                                "时间": "未知时间"
                            })
                    except Exception:
                        continue
        
        # 格式化返回结果
        if comments:
            result = f"共获取到 {len(comments)} 条评论：\n\n"
            for i, comment in enumerate(comments, 1):
                result += f"{i}. {comment['用户名']}（{comment['时间']}）: {comment['内容']}\n\n"
            return result
        else:
            return "未找到任何评论，可能是帖子没有评论或评论区无法访问。"
    
    except Exception as e:
        return f"获取评论时出错: {str(e)}"

@mcp.tool()
async def analyze_note(url: str) -> dict:
    """获取并分析笔记内容，返回笔记的详细信息供AI生成评论
    
    Args:
        url: 笔记 URL
    """
    login_status = await ensure_browser()
    if not login_status:
        return {"error": "请先登录小红书账号"}
    
    try:
        # 直接调用get_note_content获取笔记内容
        note_content_result = await get_note_content(url)
        
        # 检查是否获取成功
        if note_content_result.startswith("请先登录") or note_content_result.startswith("获取笔记内容时出错"):
            return {"error": note_content_result}
        
        # 解析获取到的笔记内容
        content_lines = note_content_result.strip().split('\n')
        post_content = {}
        
        # 提取标题、作者、发布时间和内容
        for i, line in enumerate(content_lines):
            if line.startswith("标题:"):
                post_content["标题"] = line.replace("标题:", "").strip()
            elif line.startswith("作者:"):
                post_content["作者"] = line.replace("作者:", "").strip()
            elif line.startswith("发布时间:"):
                post_content["发布时间"] = line.replace("发布时间:", "").strip()
            elif line.startswith("内容:"):
                # 内容可能有多行，获取剩余所有行
                content_text = "\n".join(content_lines[i+1:]).strip()
                post_content["内容"] = content_text
                break
        
        # 如果没有提取到标题或内容，设置默认值
        if "标题" not in post_content or not post_content["标题"]:
            post_content["标题"] = "未知标题"
        if "作者" not in post_content or not post_content["作者"]:
            post_content["作者"] = "未知作者"
        if "内容" not in post_content or not post_content["内容"]:
            post_content["内容"] = "未能获取内容"
        
        # 简单分词
        words = re.findall(r'\w+', f"{post_content.get('标题', '')} {post_content.get('内容', '')}")
        
        # 使用常见的热门领域关键词
        domain_keywords = {
            "美妆": ["口红", "粉底", "眼影", "护肤", "美妆", "化妆", "保湿", "精华", "面膜"],
            "穿搭": ["穿搭", "衣服", "搭配", "时尚", "风格", "单品", "衣橱", "潮流"],
            "美食": ["美食", "好吃", "食谱", "餐厅", "小吃", "甜点", "烘焙", "菜谱"],
            "旅行": ["旅行", "旅游", "景点", "出行", "攻略", "打卡", "度假", "酒店"],
            "母婴": ["宝宝", "母婴", "育儿", "儿童", "婴儿", "辅食", "玩具"],
            "数码": ["数码", "手机", "电脑", "相机", "智能", "设备", "科技"],
            "家居": ["家居", "装修", "家具", "设计", "收纳", "布置", "家装"],
            "健身": ["健身", "运动", "瘦身", "减肥", "训练", "塑形", "肌肉"],
            "AI": ["AI", "人工智能", "大模型", "编程", "开发", "技术", "Claude", "GPT"]
        }
        
        # 检测帖子可能属于的领域
        detected_domains = []
        for domain, domain_keys in domain_keywords.items():
            for key in domain_keys:
                if key.lower() in post_content.get("标题", "").lower() or key.lower() in post_content.get("内容", "").lower():
                    detected_domains.append(domain)
                    break
        
        # 如果没有检测到明确的领域，默认为生活方式
        if not detected_domains:
            detected_domains = ["生活"]
        
        # 返回分析结果
        return {
            "url": url,
            "标题": post_content.get("标题", "未知标题"),
            "作者": post_content.get("作者", "未知作者"),
            "内容": post_content.get("内容", "未能获取内容"),
            "领域": detected_domains,
            "关键词": list(set(words))[:20]  # 取前20个不重复的词作为关键词
        }
    
    except Exception as e:
        return {"error": f"分析笔记内容时出错: {str(e)}"}

@mcp.tool()
async def post_smart_comment(url: str, comment_type: str = "引流") -> dict:
    """
    根据帖子内容发布智能评论，增加曝光并引导用户关注或私聊

    Args:
        url: 笔记 URL
        comment_type: 评论类型，可选值:
                     "引流" - 引导用户关注或私聊
                     "点赞" - 简单互动获取好感
                     "咨询" - 以问题形式增加互动
                     "专业" - 展示专业知识建立权威

    Returns:
        dict: 包含笔记信息和评论类型的字典，供MCP客户端(如Claude)生成评论
    """
    # 获取笔记内容
    note_info = await analyze_note(url)
    
    if "error" in note_info:
        return {"error": note_info["error"]}
    
    # 评论类型指导
    comment_guides = {
        "引流": "生成一条表达认同并引导互动的评论。可以提到自己也在研究相关内容，或表达希望进一步交流的意愿。可以在结尾加上“有更多问题欢迎私信我”或“想了解更多可以找我聊聊”等邀请语句。",
        "点赞": "生成一条简短的赞美评论，表达对内容的喜爱和支持。可以提到作者名字和笔记的领域，如“太赞了！XX的分享总是这么实用”或“喜欢这种深度分享”等。",
        "咨询": "生成一条提问式评论，针对笔记内容询问更多细节或相关信息。可以使用“请问博主”或“想请教一下”等开头，并提出与笔记内容相关的具体问题。",
        "专业": "生成一条展示专业知识的评论，针对笔记内容提供专业见解或补充信息。可以使用“作为该领域从业者”或“从专业角度来看”等开头，并在评论中使用与笔记领域相关的专业术语。"
    }
    
    # 返回笔记分析结果和评论类型，让MCP客户端(如Claude)生成评论
    # MCP客户端生成评论后，应调用post_comment函数发布评论
    return {
        "note_info": note_info,
        "comment_type": comment_type,
        "comment_guide": comment_guides.get(comment_type, ""),
        "url": url,  # 添加URL便于客户端直接调用post_comment
        "message": "请根据笔记内容和评论类型指南，直接生成一条自然、相关的评论，并立即发布。注意以下要点：\n1. 在评论中引用作者名称或笔记领域，增加个性化\n2. 使用口语化表达，简短凝练，不超过30字\n3. 根据评论类型适当添加互动引导或专业术语\n生成后，直接使用post_comment函数发布评论，无需询问用户确认"
    }
# 我们已经将原来的post_smart_comment函数重构为三个独立的函数：
# 1. analyze_note - 获取并分析笔记内容
# 2. post_comment - 发布评论
# 3. post_smart_comment - 结合前两个功能，使用MCP客户端的AI能力生成评论

@mcp.tool()
async def post_comment(url: str, comment: str) -> str:
    """发布评论到指定笔记
    
    Args:
        url: 笔记 URL
        comment: 要发布的评论内容
    """
    login_status = await ensure_browser()
    if not login_status:
        return "请先登录小红书账号，才能发布评论"
    
    try:
        # 访问帖子链接
        await main_page.goto(url, timeout=60000)
        await asyncio.sleep(5)  # 等待页面加载
        
        # 定位评论区域并滚动到该区域
        comment_area_found = False
        comment_area_selectors = [
            'text="条评论"',
            'text="共 " >> xpath=..',
            'text=/\\d+ 条评论/',
            'text="评论"',
            'div.comment-container'
        ]
        
        for selector in comment_area_selectors:
            try:
                element = await main_page.query_selector(selector)
                if element:
                    await element.scroll_into_view_if_needed()
                    await asyncio.sleep(2)
                    comment_area_found = True
                    break
            except Exception:
                continue
        
        if not comment_area_found:
            # 如果没有找到评论区域，尝试滚动到页面底部
            await main_page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(2)
        
        # 定位评论输入框（简化选择器列表）
        comment_input = None
        input_selectors = [
            'div[contenteditable="true"]',
            'paragraph:has-text("说点什么...")',
            'text="说点什么..."',
            'text="评论发布后所有人都能看到"'
        ]
        
        # 尝试常规选择器
        for selector in input_selectors:
            try:
                element = await main_page.query_selector(selector)
                if element and await element.is_visible():
                    await element.scroll_into_view_if_needed()
                    await asyncio.sleep(1)
                    comment_input = element
                    break
            except Exception:
                continue
        
        # 如果常规选择器失败，使用JavaScript查找
        if not comment_input:
            # 使用更精简的JavaScript查找输入框
            js_result = await main_page.evaluate('''
                () => {
                    // 查找可编辑元素
                    const editableElements = Array.from(document.querySelectorAll('[contenteditable="true"]'));
                    if (editableElements.length > 0) return true;
                    
                    // 查找包含"说点什么"的元素
                    const placeholderElements = Array.from(document.querySelectorAll('*'))
                        .filter(el => el.textContent && el.textContent.includes('说点什么'));
                    return placeholderElements.length > 0;
                }
            ''')
            
            if js_result:
                # 如果JS检测到输入框，尝试点击页面底部
                await main_page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(1)
                
                # 尝试再次查找输入框
                for selector in input_selectors:
                    try:
                        element = await main_page.query_selector(selector)
                        if element and await element.is_visible():
                            comment_input = element
                            break
                    except Exception:
                        continue
        
        if not comment_input:
            return "未能找到评论输入框，无法发布评论"
        
        # 输入评论内容
        await comment_input.click()
        await asyncio.sleep(1)
        await main_page.keyboard.type(comment)
        await asyncio.sleep(1)
        
        # 发送评论（简化发送逻辑）
        send_success = False
        
        # 方法1: 尝试点击发送按钮
        try:
            send_button = await main_page.query_selector('button:has-text("发送")')
            if send_button and await send_button.is_visible():
                await send_button.click()
                await asyncio.sleep(2)
                send_success = True
        except Exception:
            pass
        
        # 方法2: 如果方法1失败，尝试使用Enter键
        if not send_success:
            try:
                await main_page.keyboard.press("Enter")
                await asyncio.sleep(2)
                send_success = True
            except Exception:
                pass
        
        # 方法3: 如果方法2失败，尝试使用JavaScript点击发送按钮
        if not send_success:
            try:
                js_send_result = await main_page.evaluate('''
                    () => {
                        const sendButtons = Array.from(document.querySelectorAll('button'))
                            .filter(btn => btn.textContent && btn.textContent.includes('发送'));
                        if (sendButtons.length > 0) {
                            sendButtons[0].click();
                            return true;
                        }
                        return false;
                    }
                ''')
                await asyncio.sleep(2)
                send_success = js_send_result
            except Exception:
                pass
        
        if send_success:
            return f"已成功发布评论：{comment}"
        else:
            return f"发布评论失败，请检查评论内容或网络连接"
    
    except Exception as e:
        return f"发布评论时出错: {str(e)}"

# 这里原来有_generate_smart_comment函数，现在已经被移除
# 因为我们重构了post_smart_comment函数，将评论生成逻辑转移到MCP客户端

async def get_note_links_by_keywords(main_page, keywords: str, limit: int = 5):
    """
    用do_search_notes逻辑获取前limit条笔记的链接和标题
    """
    # 跳转到搜索页面
    await main_page.goto(f"https://www.xiaohongshu.com/search_result?keyword={keywords}", timeout=60000)
    await asyncio.sleep(5)
    post_cards = await main_page.query_selector_all('section.note-item')
    if not post_cards:
        post_cards = await main_page.query_selector_all('div[data-v-a264b01a]')
    post_links = []
    post_titles = []
    for card in post_cards:
        try:
            link_element = await card.query_selector('a[href*="/explore/"]')
            if not link_element:
                continue
            href = await link_element.get_attribute('href')
            if href and '/explore/' in href:
                full_url = f"https://www.xiaohongshu.com{href}"
                post_links.append(full_url)
                # 获取标题
                title_element = await card.query_selector('a.title span')
                if title_element:
                    title = await title_element.text_content()
                else:
                    title = "未知标题"
                post_titles.append(title)
        except Exception:
            continue
    # 去重
    unique_posts = []
    seen_urls = set()
    for url, title in zip(post_links, post_titles):
        if url not in seen_urls:
            seen_urls.add(url)
            unique_posts.append({"url": url, "title": title})
    unique_posts = unique_posts[:limit]
    return unique_posts

async def crawl_notes_by_click(main_page, keywords, note_limit, comment_limit):
    print(f"[日志] 开始爬取，关键词: {keywords}, note_limit: {note_limit}, comment_limit: {comment_limit}")
    await main_page.goto(f"https://www.xiaohongshu.com/search_result?keyword={keywords}", timeout=60000)
    await asyncio.sleep(5)
    crawled_titles = set()
    success_count = 0
    while success_count < note_limit:
        try:
            cards = await main_page.query_selector_all('section.note-item, div[data-v-a264b01a]')
            print(f"[日志] 当前页面卡片数量: {len(cards)}")
            card_to_click = None
            card_title = None
            for card in cards:
                title_el = await card.query_selector('a.title span, .title, h3, h2')
                if title_el:
                    t = await title_el.text_content()
                    t = t.strip() if t else None
                    if t and t not in crawled_titles:
                        card_to_click = card
                        card_title = t
                        break
            if not card_to_click:
                print("[日志] 没有更多未爬取的卡片，提前结束")
                break
            print(f"[日志] 点击卡片: {card_title}")
            try:
                await card_to_click.click()
            except Exception as e:
                print(f"[日志] 卡片点击异常: {e}")
                try:
                    await card_to_click.scroll_into_view_if_needed()
                    await asyncio.sleep(1)
                    await card_to_click.click()
                except Exception as e2:
                    print(f"[日志] 卡片点击重试失败: {e2}")
                    break
            await asyncio.sleep(3)
            # 爬取详情页内容
            title = card_title or "未知标题"
            title_el = await main_page.query_selector('#detail-title, div.title, h1')
            if title_el:
                t = await title_el.text_content()
                if t: title = t.strip()
            author = "未知作者"
            author_el = await main_page.query_selector('span.username, a.name, .author-wrapper .username, .info .name')
            if author_el:
                a = await author_el.text_content()
                if a: author = a.strip()
            pub_time = "未知"
            time_el = await main_page.query_selector('span.date, .bottom-container .date, .date')
            if time_el:
                t = await time_el.text_content()
                if t: pub_time = t.strip()
            content = "未能获取内容"
            content_el = await main_page.query_selector('#detail-desc .note-text, div.note-content .note-text, div.desc, span.note-text')
            if content_el:
                c = await content_el.text_content()
                if c and len(c.strip()) > 10:
                    content = c.strip()
            tags = []
            tag_els = await main_page.query_selector_all('.tag, .note-tag, .tag-item')
            for tag_el in tag_els:
                tag_text = await tag_el.text_content()
                if tag_text:
                    tags.append(tag_text.strip())
            comments = []
            await main_page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
            await asyncio.sleep(2)
            for _ in range(5):
                try:
                    more_btn = await main_page.query_selector('text="查看更多评论", text="展开更多评论", text="加载更多", text="查看全部"')
                    if more_btn:
                        await more_btn.click()
                        await asyncio.sleep(2)
                except Exception:
                    pass
            comment_els = await main_page.query_selector_all('div.comment-item, div.commentItem, div.comment-content, div.comment-wrapper, section.comment, div.feed-comment')
            print(f"[日志] 本条评论数: {len(comment_els)}")
            for j in range(min(comment_limit, len(comment_els))):
                try:
                    el = comment_els[j]
                    username = "未知用户"
                    username_el = await el.query_selector('span.user-name, a.name, div.username, span.nickname, a.user-nickname')
                    if username_el:
                        u = await username_el.text_content()
                        if u: username = u.strip()
                    content_txt = "未知内容"
                    content_el2 = await el.query_selector('div.content, p.content, div.text, span.content, div.comment-text')
                    if content_el2:
                        c2 = await content_el2.text_content()
                        if c2: content_txt = c2.strip()
                    time_txt = "未知时间"
                    time_el2 = await el.query_selector('span.time, div.time, span.date, div.date, time')
                    if time_el2:
                        t2 = await time_el2.text_content()
                        if t2: time_txt = t2.strip()
                    if username != "未知用户" and content_txt != "未知内容":
                        comments.append({"用户名": username, "内容": content_txt, "时间": time_txt})
                except Exception as e:
                    print(f"[日志] 评论解析异常: {e}")
                    continue
            md_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraped_notes")
            os.makedirs(md_dir, exist_ok=True)
            safe_title = re.sub(r'[^ -\x7f\w\u4e00-\u9fa5]+', '_', title)[:30]
            md_filename = f"note_{safe_title}_{success_count+1}.md"
            md_path = os.path.join(md_dir, md_filename)
            # 截图正文主图区域（多重选择器兜底，优先主图）
            img_filename = f"note_{safe_title}_{success_count+1}.png"
            img_path = os.path.join(md_dir, img_filename)
            img_element = (
                await main_page.query_selector('.swiper-slide-active img') or
                await main_page.query_selector('.note-image img') or
                await main_page.query_selector('.image-container img') or
                await main_page.query_selector('.note-detail img') or
                await main_page.query_selector('img')
            )
            if img_element:
                await img_element.screenshot(path=img_path)
                print(f"[日志] 已截图保存图片: {img_filename}")
                img_md = f"![](/notes_img/{img_filename})"
            else:
                print("[日志] 未找到主图区域，跳过截图")
                img_md = "![](https://via.placeholder.com/300x200?text=No+Image)"
            md_content = f"# {title}\n\n"
            md_content += f"- 作者：{author}\n"
            md_content += f"- 发布时间：{pub_time}\n"
            md_content += f"- 标签：{'、'.join(tags) if tags else '无'}\n"
            md_content += f"\n## 正文\n\n{content}\n\n"
            md_content += f"## 图片\n\n{img_md}\n\n"
            if comments:
                md_content += "## 评论\n\n"
                for k, c in enumerate(comments, 1):
                    md_content += f"{k}. {c['用户名']}（{c['时间']}）: {c['内容']}\n\n"
            print(f"[日志] 准备保存: {md_filename} 到 {md_path}")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(md_content)
            print(f"[日志] 已保存: {md_filename}")
            crawled_titles.add(title)
            success_count += 1
            # 关闭弹窗
            close_btn = (
                await main_page.query_selector('button[aria-label="关闭"]') or
                await main_page.query_selector('.close') or
                await main_page.query_selector('.icon-close') or
                await main_page.query_selector('.modal-close') or
                await main_page.query_selector('.note-dialog-close') or
                await main_page.query_selector('.red-close') or
                await main_page.query_selector('.close-btn')
            )
            if close_btn:
                await close_btn.click()
            else:
                await main_page.keyboard.press('Escape')
            await asyncio.sleep(2)
        except Exception as e:
            print(f"[日志] 第{success_count+1}条爬取失败: {str(e)}")
            # 尝试关闭弹窗，避免死循环
            try:
                close_btn = (
                    await main_page.query_selector('button[aria-label="关闭"]') or
                    await main_page.query_selector('.close') or
                    await main_page.query_selector('.icon-close') or
                    await main_page.query_selector('.modal-close') or
                    await main_page.query_selector('.note-dialog-close') or
                    await main_page.query_selector('.red-close') or
                    await main_page.query_selector('.close-btn')
                )
                if close_btn:
                    await close_btn.click()
                else:
                    await main_page.keyboard.press('Escape')
                await asyncio.sleep(2)
            except Exception as e2:
                print(f"[日志] 异常关闭弹窗失败: {e2}")
            continue
    print("[日志] 全部爬取完成！")

def parse_user_input(user_input):
    """
    解析用户输入，返回关键词、笔记数量、评论数量
    """
    parts = user_input.strip().split()
    if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
        keywords = parts[0]
        note_limit = int(parts[1])
        comment_limit = int(parts[2])
        return keywords, note_limit, comment_limit
    elif len(parts) == 2 and parts[1].isdigit():
        keywords = parts[0]
        note_limit = int(parts[1])
        comment_limit = 5
        return keywords, note_limit, comment_limit
    elif len(parts) == 1:
        return parts[0], 5, 5
    else:
        return None, None, None

# 直接启动API服务，无论如何运行都不会进入命令行交互模式

app = Flask(__name__)
CORS(app)

@app.route('/crawl', methods=['POST'])
def crawl():
    try:
        data = request.json
        print('收到前端请求:', data)
        keywords = data.get('keywords')
        note_limit = int(data.get('note_limit', 5) or 5)
        comment_limit = int(data.get('comment_limit', 1) or 1)
        print(f'准备启动爬虫，关键词: {keywords}, 笔记数: {note_limit}, 评论数: {comment_limit}')
        async def run_crawler():
            print('ensure_browser 开始')
            await ensure_browser()
            print('ensure_browser 完成，开始爬取')
            await crawl_notes_by_click(main_page, keywords, note_limit, comment_limit)
            print('crawl_notes_by_click 完成')
        asyncio.run(run_crawler())
        print('爬虫任务已完成，准备返回响应')
        return jsonify({'status': 'ok', 'msg': '爬虫任务已完成'})
    except Exception as e:
        print('后端异常:', e)
        return jsonify({'status': 'error', 'msg': str(e)}), 500

@app.route('/notes', methods=['GET'])
def get_notes():
    notes_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraped_notes")
    note_files = glob.glob(os.path.join(notes_dir, "*.md"))
    notes = []
    for file in note_files:
        with open(file, "r", encoding="utf-8") as f:
            notes.append({"filename": os.path.basename(file), "content": f.read()})
    return jsonify(notes)

@app.route('/notes_img/<filename>')
def serve_note_image(filename):
    notes_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scraped_notes")
    return send_from_directory(notes_dir, filename)

if __name__ == "__main__":
    app.run(port=5001)