import test from 'node:test';
import assert from 'node:assert/strict';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { linkCitationMarkers } from "../src/chat-citations.mjs";
test('GFM renders tables, emphasis, lists and safe links without raw HTML',()=>{
 const html=renderToStaticMarkup(React.createElement(ReactMarkdown,{remarkPlugins:[remarkGfm]},'| Metric | Value |\n|---|---:|\n| Revenue | **10** |\n\n- First\n\n[Source](https://example.com)\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))'));
 assert.match(html,/<table>/);assert.match(html,/<strong>10<\/strong>/);assert.match(html,/<ul>/);assert.match(html,/href="https:\/\/example.com"/);assert.doesNotMatch(html,/javascript:/);assert.doesNotMatch(html,/<script>/);
});

test("citation markers become separate anchors and never concatenate", () => {
  const citations = [{ ordinal: 5 }, { ordinal: 9 }];
  assert.equal(linkCitationMarkers("Revenue [5][9].", citations), "Revenue [5](#citation-5)[9](#citation-9).");
  assert.equal(linkCitationMarkers("Revenue [5, 9].", citations), "Revenue [5](#citation-5)[9](#citation-9).");
  assert.equal(linkCitationMarkers("Revenue [5、9].", citations), "Revenue [5](#citation-5)[9](#citation-9).");
  assert.equal(linkCitationMarkers("Revenue [5, 99].", citations), "Revenue [5](#citation-5)[99].");
  assert.equal(linkCitationMarkers("Revenue [5].", []), "Revenue [5].");
  assert.equal(linkCitationMarkers("Revenue [3].", [{ title: "first" }, { title: "second" }, { title: "third" }]), "Revenue [3](#citation-3).");
  const html = renderToStaticMarkup(React.createElement(ReactMarkdown, { remarkPlugins: [remarkGfm] }, linkCitationMarkers("Revenue [5][9].", citations)));
  assert.equal((html.match(/<a /g) ?? []).length, 2);
  assert.match(html, /href="#citation-5"/);
  assert.match(html, /href="#citation-9"/);
  assert.doesNotMatch(html, />59</);
});
