from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url
from ArmoredVision.modules.v2.normalize import structural_compare
from ArmoredVision.modules.v2.reconcile import candidate_key
from ArmoredVision.modules.v2.shopee_search import ShopeeCandidateAPI


def log(message: str) -> None:
    print(f"[V2-SIFT] {message}", flush=True)


def load_image(url: str, max_dimension: int):
    import cv2
    import numpy as np
    import requests

    response = requests.get(
        url,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=(5, 10),
    )
    response.raise_for_status()
    image = cv2.imdecode(
        np.frombuffer(response.content, dtype=np.uint8),
        cv2.IMREAD_GRAYSCALE,
    )
    if image is None or image.size == 0:
        return None

    height, width = image.shape[:2]
    scale = min(1.0, float(max_dimension) / max(height, width))
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return image


def sift_match(reference, candidate, *, nfeatures: int, ratio: float, ransac_threshold: float) -> dict:
    import cv2

    sift = cv2.SIFT_create(nfeatures=nfeatures)
    keypoints_a, descriptors_a = sift.detectAndCompute(reference, None)
    keypoints_b, descriptors_b = sift.detectAndCompute(candidate, None)

    if descriptors_a is None or descriptors_b is None:
        return {
            "keypoints_reference": len(keypoints_a),
            "keypoints_candidate": len(keypoints_b),
            "knn_matches": 0,
            "good_matches": 0,
            "inliers": 0,
            "inlier_ratio": 0.0,
            "homography_found": False,
            "visual_score": 0.0,
        }

    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    knn = matcher.knnMatch(descriptors_a, descriptors_b, k=2)

    good = []
    for pair in knn:
        if len(pair) != 2:
            continue
        first, second = pair
        if first.distance < ratio * second.distance:
            good.append(first)

    inliers = 0
    homography_found = False
    if len(good) >= 4:
        import numpy as np

        src = np.float32([keypoints_a[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([keypoints_b[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        _, mask = cv2.findHomography(
            src,
            dst,
            cv2.RANSAC,
            ransac_threshold,
        )
        if mask is not None:
            inliers = int(mask.ravel().sum())
            homography_found = inliers >= 4

    inlier_ratio = inliers / max(1, len(good))
    # Deliberately transparent score: geometric agreement dominates raw matches.
    visual_score = min(
        1.0,
        0.65 * min(1.0, inliers / 20.0)
        + 0.25 * inlier_ratio
        + 0.10 * min(1.0, len(good) / 40.0),
    )

    return {
        "keypoints_reference": len(keypoints_a),
        "keypoints_candidate": len(keypoints_b),
        "knn_matches": len(knn),
        "good_matches": len(good),
        "inliers": inliers,
        "inlier_ratio": round(inlier_ratio, 4),
        "homography_found": homography_found,
        "visual_score": round(visual_score, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Experimento V2 de matching visual open-source com SIFT + Lowe ratio + RANSAC."
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--keyword-pages", type=int, default=2)
    parser.add_argument("--pool-limit", type=int, default=120)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--max-dimension", type=int, default=640)
    parser.add_argument("--nfeatures", type=int, default=700)
    parser.add_argument("--ratio", type=float, default=0.75)
    parser.add_argument("--ransac-threshold", type=float, default=5.0)
    args = parser.parse_args()

    for candidate in (ROOT / ".env", ROOT / "credentials" / "shopee" / "affiliate.env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)

    started = time.monotonic()
    try:
        import cv2  # noqa: F401
    except ImportError:
        print(json.dumps({
            "status": "ERROR",
            "error": "OpenCV não está instalado neste ambiente.",
        }, ensure_ascii=False, indent=2))
        return 2

    import cv2
    if not hasattr(cv2, "SIFT_create"):
        print(json.dumps({
            "status": "ERROR",
            "error": "Esta instalação do OpenCV não expõe SIFT_create.",
        }, ensure_ascii=False, indent=2))
        return 2

    log("resolvendo produto original...")
    resolved = resolve_short_url(args.url)
    original = ShopeeAffiliateAPI().get_exact_product(resolved.shop_id, resolved.item_id)
    log(f"original: {resolved.shop_id}:{resolved.item_id} | {original.get('productName')}")

    api = ShopeeCandidateAPI()
    reference_key = candidate_key(original)
    terms = []
    from ArmoredVision.modules.v2.normalize import structural_facts

    facts = structural_facts(str(original.get("productName") or ""))
    size = facts.get("size_cm")
    if size is not None:
        terms.append(f"{size:g}cm")
    if facts.get("drawers") is not None:
        terms.append("gaveta" if facts["drawers"] == 1 else f"{int(facts['drawers'])} gaveta")
    if facts.get("doors") is not None:
        terms.append("porta" if facts["doors"] == 1 else f"{int(facts['doors'])} porta")
    terms = list(dict.fromkeys(terms))

    pool: dict[tuple[str, str], dict] = {}
    keyword = " ".join(terms)
    log(f"busca visual: {keyword!r}")
    for page in range(1, args.keyword_pages + 1):
        products = api.search_products(keyword, page=page, limit=50, sort_type=1)
        before = len(pool)
        for product in products:
            key = candidate_key(product)
            if key[0] and key[1] and key != reference_key:
                pool.setdefault(key, product)
                if len(pool) >= args.pool_limit:
                    break
        log(f"  página {page}: {len(products)} | pool={len(pool)} (+{len(pool)-before})")
        if len(pool) >= args.pool_limit:
            break

    log("baixando imagem original...")
    original_url = str(original.get("imageUrl") or "")
    try:
        reference_image = load_image(original_url, args.max_dimension)
    except Exception as exc:
        print(json.dumps({
            "status": "ERROR",
            "error": f"falha ao baixar imagem original: {exc}",
        }, ensure_ascii=False, indent=2))
        return 2

    if reference_image is None:
        print(json.dumps({
            "status": "ERROR",
            "error": "imagem original inválida ou vazia",
        }, ensure_ascii=False, indent=2))
        return 2

    # Reuse one SIFT extractor for the reference and every candidate.
    sift = cv2.SIFT_create(nfeatures=args.nfeatures)
    ref_kp, ref_desc = sift.detectAndCompute(reference_image, None)
    if ref_desc is None:
        print(json.dumps({
            "status": "ERROR",
            "error": "não foi possível extrair descritores SIFT da imagem original",
            "reference_keypoints": len(ref_kp),
        }, ensure_ascii=False, indent=2))
        return 2

    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    rows = []
    products = list(pool.values())

    log(f"matching SIFT em {len(products)} candidatos...")
    for index, product in enumerate(products, 1):
        row = {
            "shop_id": str(product.get("shopId") or ""),
            "item_id": str(product.get("itemId") or ""),
            "product_name": str(product.get("productName") or ""),
            "shop_name": product.get("shopName"),
            "structural_matches": [],
            "structural_conflicts": [],
            "image_error": None,
        }
        matches, conflicts = structural_compare(original, product)
        row["structural_matches"] = matches
        row["structural_conflicts"] = conflicts

        url = str(product.get("imageUrl") or "")
        try:
            image = load_image(url, args.max_dimension)
            if image is None:
                raise ValueError("imagem inválida")
            kp, desc = sift.detectAndCompute(image, None)
            row["keypoints_candidate"] = len(kp)
            if desc is None:
                row.update({
                    "knn_matches": 0,
                    "good_matches": 0,
                    "inliers": 0,
                    "inlier_ratio": 0.0,
                    "homography_found": False,
                    "visual_score": 0.0,
                })
            else:
                knn = matcher.knnMatch(ref_desc, desc, k=2)
                good = []
                for pair in knn:
                    if len(pair) == 2 and pair[0].distance < args.ratio * pair[1].distance:
                        good.append(pair[0])

                inliers = 0
                homography_found = False
                if len(good) >= 4:
                    import numpy as np
                    src = np.float32([ref_kp[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
                    dst = np.float32([kp[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
                    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, args.ransac_threshold)
                    if mask is not None:
                        inliers = int(mask.ravel().sum())
                        homography_found = inliers >= 4

                inlier_ratio = inliers / max(1, len(good))
                visual_score = min(
                    1.0,
                    0.65 * min(1.0, inliers / 20.0)
                    + 0.25 * inlier_ratio
                    + 0.10 * min(1.0, len(good) / 40.0),
                )
                row.update({
                    "knn_matches": len(knn),
                    "good_matches": len(good),
                    "inliers": inliers,
                    "inlier_ratio": round(inlier_ratio, 4),
                    "homography_found": homography_found,
                    "visual_score": round(visual_score, 4),
                })
        except Exception as exc:
            row["image_error"] = str(exc)
            row.update({
                "knn_matches": 0,
                "good_matches": 0,
                "inliers": 0,
                "inlier_ratio": 0.0,
                "homography_found": False,
                "visual_score": 0.0,
            })

        rows.append(row)
        if index % 10 == 0 or index == len(products):
            log(f"  {index}/{len(products)}")

    rows.sort(
        key=lambda r: (
            -float(r["visual_score"]),
            -int(r["inliers"]),
            -int(r["good_matches"]),
            r["shop_id"],
            r["item_id"],
        )
    )

    payload = {
        "status": "EXPERIMENTAL_SIFT_MATCHING",
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "method": {
            "detector": "SIFT/OpenCV",
            "matcher": "BFMatcher/L2",
            "ratio_test": args.ratio,
            "ransac_threshold": args.ransac_threshold,
            "max_dimension": args.max_dimension,
            "nfeatures": args.nfeatures,
        },
        "original": {
            "shop_id": resolved.shop_id,
            "item_id": resolved.item_id,
            "product_name": original.get("productName"),
            "image_url": original.get("imageUrl"),
        },
        "pool_size": len(products),
        "top": rows[:args.top],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
