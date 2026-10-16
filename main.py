import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.auth import router as auth_router
from app.api.chat import router as chat_router
from app.api.children import router as children_router
from app.api.records import router as records_router
from app.api.doctor import router as doctor_router

# Origin được phép gọi API khi KHÔNG cấu hình gì. Cố ý là máy của lập trình viên chứ
# không phải "*": thiếu cấu hình thì đóng cửa, giống nguyên tắc đã áp cho
# INFERENCE_SERVICE_TOKEN. Triển khai thật phải khai báo tên miền thật, và việc phải
# khai báo chính là lúc người triển khai nhìn thấy danh sách này tồn tại.
ORIGIN_MAC_DINH = (
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
)


class CauHinhCORSKhongHopLe(RuntimeError):
    """Cấu hình CORS vừa sai vừa nguy hiểm nên ứng dụng từ chối khởi động."""


def doc_danh_sach_origin(gia_tri_env=None):
    """Đọc allowlist origin từ biến môi trường, dạng ngăn cách bằng dấu phẩy.

    Vì sao "*" bị TỪ CHỐI thẳng thay vì được chấp nhận rồi âm thầm tắt credentials:
        Đây là chỗ cấu hình cũ sai, và nó sai theo kiểu nhìn qua tưởng an toàn.
        allow_origins=["*"] nghe như "trả về Access-Control-Allow-Origin: *", tức là
        trình duyệt sẽ tự chặn mọi request có credentials. Nhưng khi đi kèm
        allow_credentials=True thì Starlette chuyển sang chế độ khác hẳn: nó ĐỐI CHIẾU
        origin của người gọi vào response, kèm Access-Control-Allow-Credentials: true.

        Đã đo thật, không suy đoán: gọi bằng Origin https://ke-tan-cong.example thì cả
        preflight lẫn request thường đều trả về đúng origin đó kèm allow-credentials.
        Nghĩa là bất kỳ trang web nào trên đời cũng đọc được response — đúng cái mà
        người viết dòng cấu hình đó tin rằng mình đang tránh.

        Vì hai giá trị này phối hợp với nhau theo cách trái trực giác, nên lựa chọn
        đúng là nổ ngay lúc khởi động chứ không phải tự sửa hộ: tự sửa hộ sẽ để lại một
        file cấu hình ghi "*" mà hành vi thật lại khác, và lần sau đọc file đó người ta
        vẫn hiểu sai y như lần này.
    """
    if gia_tri_env is None:
        gia_tri_env = os.getenv("CORS_ALLOW_ORIGINS", "")

    cac_origin = [o.strip() for o in gia_tri_env.split(",") if o.strip()]
    if not cac_origin:
        return list(ORIGIN_MAC_DINH)

    if "*" in cac_origin:
        raise CauHinhCORSKhongHopLe(
            "CORS_ALLOW_ORIGINS khong nhan gia tri '*'. API nay dung Authorization: Bearer, "
            "va '*' di kem allow_credentials se khien moi trang web deu goi duoc. "
            "Hay liet ke tung origin cu the, vi du: https://app.benhvien.vn"
        )
    return cac_origin


class CauHinhTaiLieuKhongHopLe(RuntimeError):
    """BAT_TAI_LIEU_API mang giá trị không đọc được nên ứng dụng từ chối khởi động."""


_GIA_TRI_BAT = {"1", "true", "yes", "on"}
_GIA_TRI_TAT = {"", "0", "false", "no", "off"}


def cau_hinh_tai_lieu_api(gia_tri_env=None) -> dict:
    """Đường dẫn /docs, /redoc, /openapi.json cho FastAPI, đọc từ BAT_TAI_LIEU_API.

    Vì sao mặc định TẮT:
        Tài liệu tương tác liệt kê mọi endpoint, mọi tham số và cả các endpoint chỉ dành cho
        worker — bản đồ đầy đủ cho người dò hệ thống, phát sẵn cho bất kỳ ai không cần đăng
        nhập. Lập trình viên cần nó thì bật trong .env; máy chủ triển khai quên cấu hình thì
        đóng, cùng nguyên tắc với INFERENCE_SERVICE_TOKEN và CORS_ALLOW_ORIGINS.

    Vì sao giá trị lạ làm nổ thay vì coi là tắt: "BAT_TAI_LIEU_API=bat" là người viết muốn
    bật. Lặng lẽ tắt khiến họ đi tìm lỗi ở chỗ khác; nổ lúc khởi động thì thấy ngay.
    """
    if gia_tri_env is None:
        gia_tri_env = os.getenv("BAT_TAI_LIEU_API", "")
    gia_tri = gia_tri_env.strip().lower()

    if gia_tri in _GIA_TRI_BAT:
        return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}
    if gia_tri in _GIA_TRI_TAT:
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    raise CauHinhTaiLieuKhongHopLe(
        f"BAT_TAI_LIEU_API={gia_tri_env!r} khong hop le. Dung 1/true/yes/on de bat, "
        "0/false/no/off hoac de trong de tat."
    )


# Header bảo mật gắn vào mọi response. Không có HSTS: header đó chỉ có nghĩa qua HTTPS, và
# uvicorn ở đây chạy HTTP sau proxy — HSTS đặt ở proxy, nơi kết thúc TLS.
HEADER_BAO_MAT = {
    # Trình duyệt không được đoán lại kiểu nội dung: ảnh tải lên không bao giờ bị chạy như HTML.
    "X-Content-Type-Options": "nosniff",
    # API không có trang nào cần nhúng vào khung của trang khác.
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}

# Riêng /api/: dữ liệu ở đây là hồ sơ bệnh án của trẻ, không được nằm lại trong cache của
# trình duyệt hay proxy dùng chung (máy tính gia đình, máy trạm ở phòng khám). CSP chặn mọi
# tài nguyên: phản hồi API là JSON hoặc ảnh, không bao giờ cần chạy script. Không áp CSP cho
# /docs vì Swagger UI tải script từ CDN.
HEADER_BAO_MAT_API = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
}


async def gan_header_bao_mat(request, call_next):
    response = await call_next(request)
    # setdefault: endpoint đã tự đặt header (vd. ảnh dùng "private, no-store") thì giữ nguyên.
    for ten, gia_tri in HEADER_BAO_MAT.items():
        response.headers.setdefault(ten, gia_tri)
    if request.url.path.startswith("/api/"):
        for ten, gia_tri in HEADER_BAO_MAT_API.items():
            response.headers.setdefault(ten, gia_tri)
    return response


class SchemaKhongKhop(RuntimeError):
    """Database chưa khớp với model của phiên bản code đang chạy."""


def kiem_schema_luc_khoi_dong(ham_kiem=None) -> None:
    """Từ chối khởi động nếu database chưa được đồng bộ với model.

    Vì sao nổ ở đây thay vì để request tự lỗi:
        Đo thật khi chạy code mới trên database chưa migrate: server khởi động bình thường,
        rồi MỌI lần đăng nhập đều trả 500 vì "no such column". Tức là hệ thống trông như
        đang chạy, cho tới khi một phụ huynh thật đụng vào. Nổ lúc khởi động thì người
        triển khai thấy lỗi ngay trong log, kèm đúng lệnh cần chạy.

    Vì sao KHÔNG tự migrate ở đây:
        Chạy nhiều worker thì mỗi worker cùng khởi động và cùng ALTER TABLE một lúc: một
        worker thành công, các worker còn lại nổ "duplicate column name". Migrate là một bước
        triển khai chạy đúng MỘT lần, không phải việc của từng tiến trình phục vụ.
    """
    if ham_kiem is None:
        from setup_database import kiem_tra_schema as ham_kiem
    lech = ham_kiem()
    if lech:
        raise SchemaKhongKhop(
            "Database chua khop voi phien ban code nay: " + "; ".join(lech)
            + ". Chay 'python setup_database.py' truoc khi khoi dong."
        )


kiem_schema_luc_khoi_dong()


def don_luc_khoi_dong():
    """Dọn dữ liệu quá hạn một lần khi server khởi động.

    Vì sao cần, dù đường chat đã tự dọn theo nhịp: server vừa khởi động sau một thời gian
    tắt có thể đang giữ dữ liệu quá hạn từ lâu, và nếu không ai chat thì nhịp dọn không bao
    giờ tới. Lỗi ở đây chỉ ghi log chứ không chặn khởi động — khác với kiểm tra schema:
    schema lệch làm MỌI request hỏng, còn một lần dọn hỏng thì lần sau vẫn dọn được.

    Vì sao đặt trong lifespan chứ không gọi thẳng ở đây như kiem_schema_luc_khoi_dong: xoá
    dữ liệu là tác dụng phụ, và nó không được xảy ra chỉ vì có người `import main`
    (test, script, công cụ xuất ngữ cảnh). Lifespan chỉ chạy khi server thật sự phục vụ.
    """
    from setup_database import SessionLocal
    from app.services import chat_history
    try:
        with SessionLocal() as db:
            return chat_history.don_dinh_ky(db)
    except Exception:
        logging.getLogger("bitss.main").exception("don du lieu qua han luc khoi dong that bai")
        return None


@asynccontextmanager
async def lifespan(_app):
    don_luc_khoi_dong()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="Pediatric Gastro AI Assistant API",
    version="1.0.0",
    description="Hệ thống Backend hỗ trợ ra quyết định lâm sàng tiêu hóa nhi khoa tích hợp Gemini Agent.",
    **cau_hinh_tai_lieu_api(),
)

app.middleware("http")(gan_header_bao_mat)

# Chỉ siết origin, KHÔNG siết methods/headers. Lỗ hổng nằm ở chỗ "ai được gọi", không
# nằm ở chỗ "gọi bằng động từ nào": một khi origin đã nằm trong danh sách tin cậy thì
# hạn chế thêm method chỉ làm frontend gãy vặt mà không thêm được gì về an toàn.
app.add_middleware(
    CORSMiddleware,
    allow_origins=doc_danh_sach_origin(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gắn router chat vào ứng dụng
app.include_router(auth_router)
app.include_router(children_router)
app.include_router(chat_router)
app.include_router(records_router)
app.include_router(doctor_router)
@app.get("/")
def read_root():
    return {"status": "online", "system": "BITSS Pediatric Gastro Assistant"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)