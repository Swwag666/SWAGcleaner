#![allow(non_snake_case, non_camel_case_types)]
use std::ffi::c_void;
use std::os::windows::ffi::OsStringExt;

pub type HANDLE = *mut c_void;
pub type HWND = *mut c_void;
pub const INVALID_HANDLE_VALUE: HANDLE = -1isize as HANDLE;

pub const FA_HIDDEN: u32 = 0x2;
pub const FA_SYSTEM: u32 = 0x4;
pub const FA_DIRECTORY: u32 = 0x10;
pub const FA_REPARSE: u32 = 0x400;

pub const FO_DELETE: u32 = 3;
pub const FOF_SILENT: u16 = 0x4;
pub const FOF_NOCONFIRMATION: u16 = 0x10;
pub const FOF_ALLOWUNDO: u16 = 0x40;
pub const FOF_NOCONFIRMMKDIR: u16 = 0x200;
pub const FOF_NOERRORUI: u16 = 0x400;

pub const SHERB_NOCONFIRMATION: u32 = 0x1;
pub const SHERB_NOPROGRESSUI: u32 = 0x2;
pub const SHERB_NOSOUND: u32 = 0x4;

pub const COINIT_APARTMENTTHREADED: u32 = 0x2;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct FILETIME {
    pub lo: u32,
    pub hi: u32,
}

impl FILETIME {
    pub fn ticks(&self) -> u64 {
        ((self.hi as u64) << 32) | self.lo as u64
    }
    pub fn unix_seconds(&self) -> i64 {
        let t = self.ticks();
        if t < 116_444_736_000_000_000 {
            0
        } else {
            ((t - 116_444_736_000_000_000) / 10_000_000) as i64
        }
    }
}

#[repr(C)]
pub struct WIN32_FIND_DATAW {
    pub dw_file_attributes: u32,
    pub ft_creation_time: FILETIME,
    pub ft_last_access_time: FILETIME,
    pub ft_last_write_time: FILETIME,
    pub n_file_size_high: u32,
    pub n_file_size_low: u32,
    pub dw_reserved0: u32,
    pub dw_reserved1: u32,
    pub c_file_name: [u16; 260],
    pub c_alternate_file_name: [u16; 14],
}

#[repr(i32)]
#[derive(Clone, Copy)]
pub enum FIND_EX_INFO_LEVEL {
    Basic = 1,
}

#[repr(i32)]
#[derive(Clone, Copy)]
pub enum FIND_EX_SEARCH_OP {
    NameMatch = 0,
}

pub const FIND_FIRST_EX_LARGE_FETCH: u32 = 0x2;

#[repr(C)]
pub struct SHFILEOPSTRUCTW {
    pub hwnd: HWND,
    pub w_func: u32,
    pub p_from: *const u16,
    pub p_to: *const u16,
    pub f_flags: u16,
    pub f_any_operations_aborted: i32,
    pub h_name_mappings: *mut c_void,
    pub lpsz_progress_title: *const u16,
}

#[link(name = "kernel32")]
extern "system" {
    fn FindFirstFileExW(
        lp_file_name: *const u16,
        f_info_level: FIND_EX_INFO_LEVEL,
        lp_find_data: *mut WIN32_FIND_DATAW,
        f_search_op: FIND_EX_SEARCH_OP,
        lp_search_filter: *mut c_void,
        f_flags: u32,
    ) -> HANDLE;
    fn FindNextFileW(h_find_file: HANDLE, lp_find_data: *mut WIN32_FIND_DATAW) -> i32;
    fn FindClose(h_find_file: HANDLE) -> i32;
    pub fn GetLastError() -> u32;
    fn GetDiskFreeSpaceExW(
        lp_root: *const u16,
        free_to_caller: *mut u64,
        total: *mut u64,
        total_free: *mut u64,
    ) -> i32;
    fn GetVolumeInformationW(
        lp_root: *const u16,
        lp_volume_name: *mut u16,
        volume_name_size: u32,
        lp_serial: *mut u32,
        lp_max_component: *mut u32,
        lp_fs_flags: *mut u32,
        lp_fs_name: *mut u16,
        fs_name_size: u32,
    ) -> i32;
}

#[link(name = "shell32")]
extern "system" {
    fn SHFileOperationW(lp: *const SHFILEOPSTRUCTW) -> i32;
    fn SHEmptyRecycleBinW(hwnd: HWND, root: *const u16, flags: u32) -> i32;
}

#[link(name = "ole32")]
extern "system" {
    fn CoInitializeEx(reserved: *mut c_void, model: u32) -> i32;
    fn CoUninitialize();
}

pub fn wide(s: &str) -> Vec<u16> {
    let mut v: Vec<u16> = s.encode_utf16().collect();
    v.push(0);
    v
}

pub fn string_from_wide(buf: &[u16]) -> String {
    let end = buf.iter().position(|&c| c == 0).unwrap_or(buf.len());
    std::ffi::OsString::from_wide(&buf[..end])
        .to_string_lossy()
        .into_owned()
}

pub fn find_first(pattern: &str) -> Result<(HANDLE, WIN32_FIND_DATAW), u32> {
    let pat = wide(pattern);
    let mut data = unsafe { std::mem::zeroed::<WIN32_FIND_DATAW>() };
    let h = unsafe {
        FindFirstFileExW(
            pat.as_ptr(),
            FIND_EX_INFO_LEVEL::Basic,
            &mut data,
            FIND_EX_SEARCH_OP::NameMatch,
            std::ptr::null_mut(),
            FIND_FIRST_EX_LARGE_FETCH,
        )
    };
    if h == INVALID_HANDLE_VALUE {
        Err(unsafe { GetLastError() })
    } else {
        Ok((h, data))
    }
}

pub fn find_next(h: HANDLE, data: &mut WIN32_FIND_DATAW) -> bool {
    unsafe { FindNextFileW(h, data) != 0 }
}

pub fn find_close(h: HANDLE) {
    unsafe {
        FindClose(h);
    }
}

pub fn find_name(data: &WIN32_FIND_DATAW) -> String {
    string_from_wide(&data.c_file_name)
}

pub fn find_size(data: &WIN32_FIND_DATAW) -> u64 {
    ((data.n_file_size_high as u64) << 32) | data.n_file_size_low as u64
}

pub struct DiskSpace {
    pub free_to_caller: u64,
    pub total: u64,
    pub total_free: u64,
}

pub fn disk_free(root: &str) -> Option<DiskSpace> {
    let w = wide(root);
    let (mut avail, mut total, mut total_free) = (0u64, 0u64, 0u64);
    let ok = unsafe { GetDiskFreeSpaceExW(w.as_ptr(), &mut avail, &mut total, &mut total_free) };
    if ok != 0 {
        Some(DiskSpace {
            free_to_caller: avail,
            total,
            total_free,
        })
    } else {
        None
    }
}

pub struct VolumeInfo {
    pub fs_name: String,
    pub label: String,
}

pub fn volume_info(root: &str) -> Option<VolumeInfo> {
    let w = wide(root);
    let mut fs_buf = [0u16; 64];
    let mut label_buf = [0u16; 64];
    let (mut serial, mut max_comp, mut flags) = (0u32, 0u32, 0u32);
    let ok = unsafe {
        GetVolumeInformationW(
            w.as_ptr(),
            label_buf.as_mut_ptr(),
            label_buf.len() as u32,
            &mut serial,
            &mut max_comp,
            &mut flags,
            fs_buf.as_mut_ptr(),
            fs_buf.len() as u32,
        )
    };
    if ok != 0 {
        Some(VolumeInfo {
            fs_name: string_from_wide(&fs_buf).to_uppercase(),
            label: string_from_wide(&label_buf),
        })
    } else {
        None
    }
}

pub fn co_initialize() {
    unsafe {
        CoInitializeEx(std::ptr::null_mut(), COINIT_APARTMENTTHREADED);
    }
}

pub fn co_uninitialize() {
    unsafe {
        CoUninitialize();
    }
}

pub fn paths_to_double_null(paths: &[String]) -> Vec<u16> {
    let mut v: Vec<u16> = Vec::new();
    for p in paths {
        v.extend(p.encode_utf16());
        v.push(0);
    }
    v.push(0);
    v
}

pub fn shell_delete(paths: &[String], allow_undo: bool) -> Result<(), (i32, bool)> {
    let rc_ab = shell_delete_raw(paths, allow_undo);
    if rc_ab.is_ok() {
        return rc_ab;
    }
    // Длинные пути (>260) и часть UNC SHFileOperation не берёт в сыром виде —
    // повтор с verbatim-префиксом (\\?\...). Дёшево, только при ошибке.
    let verbatim: Vec<String> = paths.iter().map(|p| crate::pathx::to_verbatim(p)).collect();
    shell_delete_raw(&verbatim, allow_undo)
}

fn shell_delete_raw(paths: &[String], allow_undo: bool) -> Result<(), (i32, bool)> {
    let buf = paths_to_double_null(paths);
    let title = wide("");
    let mut op = SHFILEOPSTRUCTW {
        hwnd: std::ptr::null_mut(),
        w_func: FO_DELETE,
        p_from: buf.as_ptr(),
        p_to: std::ptr::null(),
        f_flags: FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR
            | if allow_undo { FOF_ALLOWUNDO } else { 0 },
        f_any_operations_aborted: 0,
        h_name_mappings: std::ptr::null_mut(),
        lpsz_progress_title: title.as_ptr(),
    };
    let rc = unsafe { SHFileOperationW(&mut op) };
    if rc == 0 && op.f_any_operations_aborted == 0 {
        Ok(())
    } else {
        Err((rc, op.f_any_operations_aborted != 0))
    }
}

pub fn empty_recycle_bin(drive: Option<&str>) -> Result<(), i32> {
    let w = drive.map(|d| wide(&format!("{}\\", d)));
    let ptr = match &w {
        Some(v) => v.as_ptr(),
        None => std::ptr::null(),
    };
    let rc = unsafe {
        SHEmptyRecycleBinW(
            std::ptr::null_mut(),
            ptr,
            SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND,
        )
    };
    if rc == 0 || rc == -2147024865 || rc == -2147418113 {
        Ok(())
    } else {
        Err(rc)
    }
}

pub fn winerror_text(code: u32) -> &'static str {
    match code {
        2 => "ERROR_FILE_NOT_FOUND",
        3 => "ERROR_PATH_NOT_FOUND",
        5 => "ERROR_ACCESS_DENIED",
        18 => "ERROR_NO_MORE_FILES",
        21 => "ERROR_NOT_READY",
        32 => "ERROR_SHARING_VIOLATION",
        33 => "ERROR_LOCK_VIOLATION",
        // DE_* из shellapi: SHFileOperation возвращает не Win32-коды,
        // а свои (0x71+), без таблицы это читается как UNKNOWN.
        0x71 => "DE_INSRCDST",
        0x72 => "DE_MANY_SRC",
        0x74 => "DE_ROOTDIR",
        0x75 => "DE_OPCANCELLED",
        0x76 => "DE_DESTSUBTREE",
        0x78 => "DE_ACCESSDENIEDSRC",
        0x7A => "DE_PATHTOODEEP",
        0x7C => "DE_INVALIDFILES",
        0x7D => "DE_DESTSAMETREE",
        0x7E => "DE_FLDDOESTOODEEP",
        0x80 => "DE_MANYDEST",
        0x81 => "DE_INVALIDFILES",
        0x82 => "DE_SAMEFILE",
        0x83 => "DE_RENAM_REPLACE",
        0x84 => "DE_DIFFDIR",
        0x85 => "DE_ROOTDIR|DE_DESTSUBTREE",
        87 => "ERROR_INVALID_PARAMETER",
        145 => "ERROR_DIRECTORY_NOT_EMPTY",
        206 => "ERROR_FILENAME_EXCED_RANGE",
        267 => "ERROR_DIRECTORY",
        1200 => "ERROR_BAD_NETPATH",
        1223 => "ERROR_CANCELLED",
        _ => "UNKNOWN",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn double_null_joins_paths() {
        let v = paths_to_double_null(&["a".into(), "b".into()]);
        assert_eq!(v, vec!['a' as u16, 0, 'b' as u16, 0, 0]);
    }

    #[test]
    fn wide_appends_null() {
        assert_eq!(wide("ab"), vec!['a' as u16, 'b' as u16, 0]);
    }

    #[test]
    fn filetime_epoch_gives_zero() {
        assert_eq!(FILETIME { lo: 0, hi: 0 }.unix_seconds(), 0);
    }

    #[test]
    fn disk_free_on_c() {
        let d = disk_free("C:\\").expect("C должна читаться");
        assert!(d.total > 0);
    }

    #[test]
    fn volume_name_is_ntfs_or_empty() {
        if let Some(v) = volume_info("C:\\") {
            assert!(!v.fs_name.is_empty());
        }
    }
}
