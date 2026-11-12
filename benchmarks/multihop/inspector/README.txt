本地检查器：python3 benchmarks/multihop/inspector/serve.py --port 8766
服务只绑定127.0.0.1，输出目录为artifacts/visualizations/wiki2-source-inspector。
index.html：full-native-v8原始底库，完整1000题/6119段/29183事实。
indexing.html：整段索引20段小试；失败也展示，不等于正式QA评测。
原文、原始模型输出、查询检查与人工记录全部只读；页面无API调用。
