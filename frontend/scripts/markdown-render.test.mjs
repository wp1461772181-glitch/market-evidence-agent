import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
test('GFM renders tables, emphasis, lists and safe links without raw HTML',()=>{
 const html=renderToStaticMarkup(React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm]},'| Metric | Value |\n|---|---:|\n| Revenue | **10** |\n\n- First\n\n[Source](https://example.com)\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))'));
 assert.match(html,/<table>/);assert.match(html,/<strong>10<\/strong>/);assert.match(html,/<ul>/);assert.match(html,/href="https:\/\/example.com"/);assert.doesNotMatch(html,/javascript:/);assert.doesNotMatch(html,/<script>/);
});
