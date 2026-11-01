const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/ux/payment-experience.js'), 'utf8');
const receipt = source.slice(source.indexOf('async function receipt('), source.indexOf('async function qrPayment('));
const nodes = [];
let printed = '';
const context = {
  document: {createElement(tag) {const n = {tag, style: {}, append() {}}; nodes.push(n); return n;}},
  window: {open() {return {document: {write(html) {printed = html;}, close() {}}, print() {}};}},
  esc: x => String(x), sheet: () => ({close() {}}), toast() {}, setTimeout() {},
};
vm.createContext(context);
(async () => {
  await vm.runInContext(receipt + '; receipt("测试收据", [["订单", "TEST-01"], ["金额", "399"]]);', context);
  nodes.find(n => n.textContent === '打印 / 保存为 PDF').onclick();
  assert.match(printed, /测试收据/);
  assert.match(printed, /TEST-01/);
  assert.match(printed, /399/);
  assert.match(printed, /付款结果已由服务端确认/);
  console.log('RECEIPT PRINT CONTENT PASS');
})();
