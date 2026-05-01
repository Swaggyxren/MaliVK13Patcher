import com.android.build.gradle.tasks.MergeSourceSetFolders
import org.gradle.api.tasks.Copy

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}

android {
    namespace = "io.github.swaggyxren.malivk13patcher"
    compileSdk = 35

    defaultConfig {
        applicationId = "io.github.swaggyxren.malivk13patcher"
        minSdk = 26
        targetSdk = 35
        versionCode = 3
        versionName = "1.0.2"
        ndk {
            abiFilters.add("arm64-v8a")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    packaging {
        // Important: bundled native binaries must NOT be compressed in the APK
        // and must be extracted on install so we can exec them.
        jniLibs {
            useLegacyPackaging = true
        }
    }

    sourceSets {
        getByName("main") {
            // Reuse the canonical payload tree from the repo root so we never
            // duplicate the ~70 MB of Mali blobs.
            assets.srcDirs(
                "src/main/assets",
                rootProject.layout.projectDirectory.dir("../payload").asFile,
            )
        }
    }

    signingConfigs {
        // Explicitly enable v1 + v2 + v3 signing schemes on the debug keystore
        // so the produced APK installs cleanly on Android 14/15/16, which all
        // require a v2 (or v3) signature.
        getByName("debug") {
            enableV1Signing = true
            enableV2Signing = true
            enableV3Signing = true
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            isShrinkResources = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

dependencies {
    val composeBom = platform("androidx.compose:compose-bom:2024.09.02")
    implementation(composeBom)

    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.documentfile:documentfile:1.0.1")

    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.3")

    debugImplementation("androidx.compose.ui:ui-tooling")
}

// -----------------------------------------------------------------------------
// Pre-build task: rename our bundled Android arm64 binaries (extract.erofs etc.)
// into the lib*.so naming scheme that Android's jniLibs packager requires, then
// drop them into a generated jniLibs source set so they ship inside the APK.
// -----------------------------------------------------------------------------
val nativeBinariesSrc =
    rootProject.layout.projectDirectory.dir("../bin/Android/aarch64").asFile
// jniLibs.srcDirs points at the parent directory that *contains* per-ABI
// folders (e.g. arm64-v8a/), so the Copy task writes into a child folder.
val jniLibsRoot =
    layout.buildDirectory.dir("generated/jniLibs")
val nativeBinariesDst =
    layout.buildDirectory.dir("generated/jniLibs/arm64-v8a")

val nativeBinaryRenames =
    mapOf(
        "extract.erofs" to "libextract_erofs.so",
        "mkfs.erofs" to "libmkfs_erofs.so",
        "mke2fs" to "libmke2fs.so",
        "e2fsdroid" to "libe2fsdroid.so",
        "img2simg" to "libimg2simg.so",
    )

val prepareNativeBinaries =
    tasks.register<Copy>("prepareNativeBinaries") {
        from(nativeBinariesSrc) {
            for ((src, dst) in nativeBinaryRenames) {
                include(src)
                rename(src, dst)
            }
        }
        into(nativeBinariesDst)
    }

android.sourceSets.getByName("main").jniLibs.srcDirs(
    jniLibsRoot,
)

tasks.withType<MergeSourceSetFolders>().configureEach {
    dependsOn(prepareNativeBinaries)
}
