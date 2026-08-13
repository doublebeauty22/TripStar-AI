# TripStar-AI

根拠付き POI、ペース認識型プランニング、決定論的検証、安全な対象限定リビジョンを備えた AI 旅行計画ポートフォリオです。

[Portfolio README](README.md) · [English](README_en.md) · 公開デモ：**準備中**

## 主な変更

この派生プロジェクトでは、上流 TripStar を基盤として、Preference Profile、provider 可観測性、grounding/provenance、daily-load policy、pacing-aware Planner、Validator、fail-closed revision、protected-day patch、offline evaluation、blind paired review、public-demo hardening、Render 向け単一サービス構成を追加しました。

## 評価サマリー

- controlled paired evaluation：4 件
- human pacing delta 平均：**+1.00**
- pacing が改善した pair：**3/4**
- 明示的制約の保持：**4/4**
- blind verdict：**3 BETTER / 1 MIXED**
- targeted revision：**2 committed / 2 safely rejected**
- Kyoto control で duration compression の過剰最適化リスクを検出

これらは記述的な controlled evaluation であり、統計的有意性、因果効果、production traffic の結果、または普遍的優位性を主張するものではありません。詳細は [README](README.md) と [Phase 4D report](eval/phase4d/phase4d_final_report.md) を参照してください。

## 実行

```bash
cp backend/.env.example backend/.env
docker compose up --build
```

## License / Attribution

TripStar-AI は GNU GPL v2 で配布される、[1sdv の TripStar](https://github.com/1sdv/TripStar) を大幅に変更した派生プロジェクトです。上流の Git history と、保持されたコードに適用される第三者 notice を維持しています。詳細は [LICENSE](LICENSE) を参照してください。
